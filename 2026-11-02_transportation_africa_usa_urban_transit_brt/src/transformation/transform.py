"""Transformation stage of the Urban Transit & BRT Africa-USA pipeline.

Reads the raw files landed by ``src/ingestion/ingest.py`` in ``data/raw/``
(preferring the newest timestamped snapshot of each dataset and falling back
to the canonical un-suffixed file), then:

1. Cleans the BRT inventory and the four World Bank indicator tables
   (null handling, type casting, trimming, de-duplication).
2. Combines the World Bank indicators into one long ``urban_indicators``
   table keyed by ``(iso3, year)``.
3. Merges each BRT system with the latest available urban indicators for its
   country (join on ISO3 code).
4. Derives performance features: ``ridership_per_km``,
   ``ridership_per_station``, ``fleet_utilization_ratio`` (daily riders per
   fleet vehicle), ``system_age_years`` and ``ridership_per_1k_urban_pop``.

Outputs are written to ``data/processed/``:
``brt_systems_clean.csv``, ``urban_indicators_clean.csv`` and
``brt_enriched.csv``.
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("transformation")

RAW_STEMS = {
    "brt": "brt_systems_africa_usa",
    "urban_population": "wb_urban_population",
    "urban_growth_rate": "wb_urban_growth_rate",
    "gdp_per_capita": "wb_gdp_per_capita",
    "urban_pct_total": "wb_urban_pct_total",
}
WB_VALUE_COLUMNS = {
    "urban_population": "urban_population",
    "urban_growth_rate": "urban_growth_rate_pct",
    "gdp_per_capita": "gdp_per_capita_usd",
    "urban_pct_total": "urban_pct_of_total",
}
BRT_NUMERIC = ["corridor_length_km", "daily_ridership", "num_stations", "fleet_size"]
BRT_TEXT = ["city", "country", "iso3", "continent", "system_name", "status", "operator", "source"]


def load_config(path=CONFIG_PATH):
    """Load the YAML pipeline configuration.

    Args:
        path: Path to ``config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_dirs(config):
    """Resolve raw and processed directories, honouring ``DATA_DIR``.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        tuple[Path, Path]: ``(raw_dir, processed_dir)``; processed is created.
    """
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        raw_dir, processed_dir = Path(data_dir) / "raw", Path(data_dir) / "processed"
    else:
        raw_dir = PROJECT_ROOT / config["paths"]["raw_data"]
        processed_dir = PROJECT_ROOT / config["paths"]["processed_data"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def latest_raw_file(raw_dir, stem):
    """Find the newest snapshot ``<stem>_<YYYYMMDDTHHMMSSZ>.csv`` or ``<stem>.csv``.

    Args:
        raw_dir: Directory containing raw files.
        stem: Dataset filename stem.

    Returns:
        Path: Path to the file to load.

    Raises:
        FileNotFoundError: If neither a snapshot nor the canonical file exists.
    """
    pattern = re.compile(rf"^{re.escape(stem)}_\d{{8}}T\d{{6}}Z\.csv$")
    snapshots = sorted(p for p in Path(raw_dir).glob(f"{stem}_*.csv") if pattern.match(p.name))
    if snapshots:
        return snapshots[-1]
    canonical = Path(raw_dir) / f"{stem}.csv"
    if canonical.exists():
        return canonical
    raise FileNotFoundError(f"No raw file found for '{stem}' in {raw_dir}")


def load_raw_data(raw_dir):
    """Load every raw dataset into a dictionary of DataFrames.

    World Bank files that are missing or empty are replaced by empty frames
    with the expected columns so downstream steps still run.

    Args:
        raw_dir: Directory containing raw files.

    Returns:
        dict[str, pandas.DataFrame]: Keys ``brt`` and each indicator name.
    """
    frames = {}
    for key, stem in RAW_STEMS.items():
        try:
            path = latest_raw_file(raw_dir, stem)
        except FileNotFoundError:
            if key == "brt":
                raise
            logger.warning("Missing raw World Bank file for %s; using empty frame", key)
            frames[key] = pd.DataFrame(columns=["country_id", "country_name", "iso3", "year", WB_VALUE_COLUMNS[key]])
            continue
        frames[key] = pd.read_csv(path)
        logger.info("Loaded %-18s %5d rows from %s", key, len(frames[key]), path.name)
    return frames


def clean_brt_data(df):
    """Clean the BRT systems inventory.

    Trims text, upper-cases ISO3, casts ``year_opened`` to nullable int and
    numeric measures to float, treats non-positive measures as missing,
    drops rows without a system name or ISO3, and de-duplicates on
    ``(system_name, city)`` keeping the last occurrence.

    Args:
        df: Raw BRT DataFrame.

    Returns:
        pandas.DataFrame: Cleaned BRT data with a stable ``system_id``.
    """
    out = df.copy()
    for col in BRT_TEXT:
        if col in out.columns:
            out[col] = out[col].astype("string").str.strip()
    out["iso3"] = out["iso3"].str.upper()
    out["status"] = out["status"].fillna("Unknown")
    out["operator"] = out["operator"].fillna("Unknown")
    out["year_opened"] = pd.to_numeric(out["year_opened"], errors="coerce").round().astype("Int64")
    for col in BRT_NUMERIC:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        out.loc[out[col] <= 0, col] = np.nan
    before = len(out)
    out = out.dropna(subset=["system_name", "iso3"])
    out = out.drop_duplicates(subset=["system_name", "city"], keep="last")
    out = out.sort_values(["continent", "country", "city"]).reset_index(drop=True)
    out.insert(0, "system_id", range(1, len(out) + 1))
    logger.info("BRT cleaned: %d -> %d rows", before, len(out))
    return out


def clean_worldbank_data(frames):
    """Clean the four World Bank indicator tables and combine them.

    Each table gets ISO3 upper-cased, ``year`` cast to int and the value cast
    to float; rows with no value are dropped and duplicates on
    ``(iso3, year)`` removed. Tables are then outer-joined into a single
    frame keyed by ``(iso3, year)``.

    Args:
        frames: Dictionary from :func:`load_raw_data`.

    Returns:
        pandas.DataFrame: Columns ``iso3, country_name, year`` plus the four
        indicator value columns.
    """
    combined = None
    for key, value_col in WB_VALUE_COLUMNS.items():
        df = frames[key].copy()
        if df.empty:
            df = pd.DataFrame(columns=["iso3", "country_name", "year", value_col])
        df["iso3"] = df["iso3"].astype("string").str.strip().str.upper()
        df["country_name"] = df["country_name"].astype("string").str.strip()
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df[value_col] = pd.to_numeric(df[value_col], errors="coerce").astype(float)
        df = df.dropna(subset=["iso3", "year", value_col])
        df["year"] = df["year"].astype(int)
        df = df.drop_duplicates(subset=["iso3", "year"], keep="last")
        df = df[["iso3", "country_name", "year", value_col]]
        if combined is None:
            combined = df
        else:
            combined = combined.merge(df, on=["iso3", "year"], how="outer", suffixes=("", "_dup"))
            combined["country_name"] = combined["country_name"].fillna(combined["country_name_dup"])
            combined = combined.drop(columns=["country_name_dup"])
    combined = combined.sort_values(["iso3", "year"]).reset_index(drop=True)
    logger.info("Urban indicators combined: %d rows, %d countries", len(combined), combined["iso3"].nunique())
    return combined


def merge_datasets(brt_df, urban_df):
    """Left-join each BRT system with its country's latest urban indicators.

    For every country the most recent year with a non-null urban population
    is used (falling back to the most recent year of any indicator).
    BRT systems in countries without World Bank coverage keep null indicator
    values.

    Args:
        brt_df: Cleaned BRT DataFrame.
        urban_df: Combined urban indicators DataFrame.

    Returns:
        pandas.DataFrame: BRT rows with ``wb_year`` and indicator columns.
    """
    if urban_df.empty:
        latest = pd.DataFrame(columns=["iso3", "wb_year"] + list(WB_VALUE_COLUMNS.values()))
    else:
        ranked = urban_df.assign(_has_pop=urban_df["urban_population"].notna().astype(int))
        ranked = ranked.sort_values(["iso3", "_has_pop", "year"])
        latest = ranked.groupby("iso3", as_index=False).tail(1)
        latest = latest.rename(columns={"year": "wb_year"}).drop(columns=["_has_pop", "country_name"])
    merged = brt_df.merge(latest, on="iso3", how="left")
    matched = merged["wb_year"].notna().sum()
    logger.info("Merged BRT with urban indicators: %d/%d systems matched", matched, len(merged))
    return merged


def enrich_features(df, reference_year=None):
    """Add derived transit performance features.

    Features:
        * ``ridership_per_km``: daily riders per corridor km.
        * ``ridership_per_station``: daily riders per station.
        * ``fleet_utilization_ratio``: daily riders per fleet vehicle.
        * ``km_per_station``: average station spacing.
        * ``system_age_years``: years since opening.
        * ``ridership_per_1k_urban_pop``: daily riders per 1,000 urban
          residents of the country (null when no World Bank match).

    Divisions by zero or missing denominators produce nulls.

    Args:
        df: Merged BRT DataFrame.
        reference_year: Year used for system age (defaults to current UTC year).

    Returns:
        pandas.DataFrame: Enriched copy of ``df``.
    """
    out = df.copy()
    reference_year = reference_year or datetime.now(timezone.utc).year

    def safe_div(num, den):
        """Divide two Series, returning NaN where the denominator is 0/NaN."""
        den = den.replace(0, np.nan)
        return (num / den).astype(float)

    out["ridership_per_km"] = safe_div(out["daily_ridership"], out["corridor_length_km"]).round(2)
    out["ridership_per_station"] = safe_div(out["daily_ridership"], out["num_stations"]).round(2)
    out["fleet_utilization_ratio"] = safe_div(out["daily_ridership"], out["fleet_size"]).round(2)
    out["km_per_station"] = safe_div(out["corridor_length_km"], out["num_stations"]).round(3)
    out["system_age_years"] = (reference_year - out["year_opened"].astype(float)).astype(float)
    urban_pop = out["urban_population"] if "urban_population" in out else pd.Series(np.nan, index=out.index)
    out["ridership_per_1k_urban_pop"] = safe_div(out["daily_ridership"] * 1000, urban_pop.astype(float)).round(3)
    return out


def run_transformation(config=None):
    """Execute the full transformation stage and write processed CSVs.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        dict[str, Path]: Mapping of output name to written path.
    """
    config = config or load_config()
    raw_dir, processed_dir = resolve_dirs(config)
    frames = load_raw_data(raw_dir)

    brt_clean = clean_brt_data(frames["brt"])
    urban_clean = clean_worldbank_data(frames)
    enriched = enrich_features(merge_datasets(brt_clean, urban_clean))

    outputs = {
        "brt_systems": (brt_clean, processed_dir / "brt_systems_clean.csv"),
        "urban_indicators": (urban_clean, processed_dir / "urban_indicators_clean.csv"),
        "brt_enriched": (enriched, processed_dir / "brt_enriched.csv"),
    }
    written = {}
    for name, (frame, path) in outputs.items():
        frame.to_csv(path, index=False)
        logger.info("Wrote %-16s %4d rows -> %s", name, len(frame), path)
        written[name] = path
    return written


def main():
    """CLI entry point: configure logging and run the transformation."""
    config = load_config()
    log_cfg = config.get("logging", {})
    logging.basicConfig(level=log_cfg.get("level", "INFO"), format=log_cfg.get("format"))
    for name, path in run_transformation(config).items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
