"""Transformation stage for the Africa & USA Railway Networks pipeline.

Reads the five raw World Bank CSVs from ``data/raw/``, then:

1. cleans each series - trims text, upper-cases ISO2 codes, casts ``year`` to
   int and values to float, drops nulls / non-positive values / out-of-range
   years, and de-duplicates on ``(country_code, year)``;
2. merges all series into one country-year master table (outer join);
3. enriches it with region / sub-region labels, an ``is_africa`` flag,
   forward-filled rail route-km and LPI (slowly-changing series), and derived
   rail metrics: freight density (million ton-km per route-km), passenger
   density, freight share of rail traffic and year-over-year growth rates;
4. builds one-row-per-country profiles with the latest available values.

Outputs::

    data/processed/railway_master.csv
    data/processed/country_profiles.csv
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
KEYS = ["country_code", "year"]

logger = logging.getLogger("railway.transform")


def load_config(path: Optional[str] = None) -> Dict:
    """Load the YAML configuration (``CONFIG_PATH`` env var overrides the default)."""
    config_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def region_lookup(config: Dict) -> pd.DataFrame:
    """Return a ``country_code -> region, subregion, is_africa`` lookup table."""
    rows = []
    for subregion, codes in config["regions"].items():
        is_africa = subregion.endswith("Africa")
        for code in codes:
            rows.append({
                "country_code": code,
                "region": "Africa" if is_africa else "USA",
                "subregion": subregion,
                "is_africa": int(is_africa),
            })
    return pd.DataFrame(rows).drop_duplicates("country_code")


def clean_series(df: pd.DataFrame, value_column: str, min_year: int, max_year: int) -> pd.DataFrame:
    """Clean one raw indicator frame and return ``country_code, country_name, year, value``."""
    expected = {"country_code", "country_name", "year", value_column}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"Raw file for {value_column} is missing columns {sorted(missing)}")
    out = df[list(expected)].copy()
    out["country_code"] = out["country_code"].astype(str).str.strip().str.upper()
    out["country_name"] = out["country_name"].astype(str).str.strip()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out[value_column] = pd.to_numeric(out[value_column], errors="coerce")
    before = len(out)
    out = out.dropna(subset=["country_code", "year", value_column])
    out = out[(out[value_column] > 0) & out["year"].between(min_year, max_year)]
    out["year"] = out["year"].astype(int)
    out = (out.sort_values(KEYS)
              .drop_duplicates(subset=KEYS, keep="last")
              .reset_index(drop=True))
    logger.info("%-28s %4d raw -> %4d clean rows", value_column, before, len(out))
    return out[["country_code", "country_name", "year", value_column]]


def read_csv_safe(path: Path) -> pd.DataFrame:
    """Read a CSV keeping ISO2 code "NA" (Namibia) as text instead of a missing value."""
    return pd.read_csv(path, dtype={"country_code": str}, keep_default_na=False,
                       na_values=["", "NaN", "nan", "null", "None"])


def read_raw(config: Dict, raw_dir: Path) -> Dict[str, pd.DataFrame]:
    """Read and clean every configured raw CSV; missing files yield empty frames."""
    pipe = config["pipeline"]
    frames: Dict[str, pd.DataFrame] = {}
    for name, spec in config["worldbank"]["indicators"].items():
        col = spec["value_column"]
        path = raw_dir / spec["output_file"]
        if not path.exists():
            logger.warning("Raw file not found, continuing without it: %s", path)
            frames[name] = pd.DataFrame(columns=["country_code", "country_name", "year", col])
            continue
        frames[name] = clean_series(read_csv_safe(path), col,
                                    int(pipe["min_year"]), int(pipe["max_year"]))
    return frames


def merge_series(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join all indicator frames on ``(country_code, year)`` with one name column."""
    names = pd.concat([f[["country_code", "country_name"]] for f in frames.values()],
                      ignore_index=True).drop_duplicates("country_code", keep="first")
    master: Optional[pd.DataFrame] = None
    for frame in frames.values():
        values = frame.drop(columns="country_name")
        master = values if master is None else master.merge(values, on=KEYS, how="outer")
    master = master.merge(names, on="country_code", how="left")
    return master.sort_values(KEYS).reset_index(drop=True)


def enrich(master: pd.DataFrame, regions: pd.DataFrame, ffill_limit: int) -> pd.DataFrame:
    """Add region labels, forward-filled series and derived rail-efficiency metrics."""
    df = master.merge(regions, on="country_code", how="left")
    df["region"] = df["region"].fillna("Other")
    df["subregion"] = df["subregion"].fillna("Other")
    df["is_africa"] = df["is_africa"].fillna(0).astype(int)

    grouped = df.groupby("country_code", group_keys=False)
    df["rail_lines_km_filled"] = grouped["rail_lines_km"].ffill(limit=ffill_limit)
    df["lpi_infrastructure_score_filled"] = grouped["lpi_infrastructure_score"].ffill(limit=ffill_limit)

    df["freight_density_mtkm_per_km"] = df["rail_freight_million_tkm"] / df["rail_lines_km_filled"]
    df["passenger_density_mpkm_per_km"] = df["rail_passengers_million_km"] / df["rail_lines_km_filled"]
    traffic = df["rail_freight_million_tkm"] + df["rail_passengers_million_km"]
    df["freight_share_of_traffic_pct"] = 100 * df["rail_freight_million_tkm"] / traffic

    for col, out in [("rail_lines_km", "rail_lines_yoy_pct"),
                     ("rail_freight_million_tkm", "rail_freight_yoy_pct"),
                     ("rail_passengers_million_km", "rail_passengers_yoy_pct")]:
        prev_val = df.groupby("country_code")[col].shift(1)
        prev_year = df.groupby("country_code")["year"].shift(1)
        consecutive = (df["year"] - prev_year) == 1
        df[out] = np.where(consecutive, 100 * (df[col] - prev_val) / prev_val, np.nan)

    df["has_rail_data"] = df[["rail_lines_km", "rail_freight_million_tkm",
                              "rail_passengers_million_km"]].notna().any(axis=1).astype(int)
    numeric = df.select_dtypes(include="number").columns.drop(["year", "is_africa", "has_rail_data"])
    df[numeric] = df[numeric].replace([np.inf, -np.inf], np.nan).round(4)
    ordered = ["country_code", "country_name", "region", "subregion", "is_africa", "year"]
    return df[ordered + [c for c in df.columns if c not in ordered]]


def build_country_profiles(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the master table into one row per country with latest non-null values."""
    def latest(col: str) -> pd.DataFrame:
        sub = df.dropna(subset=[col]).sort_values("year").groupby("country_code").tail(1)
        return sub[["country_code", col, "year"]].rename(columns={"year": f"{col}_year"})

    base = df[["country_code", "country_name", "region", "subregion", "is_africa"]] \
        .drop_duplicates("country_code")
    for col in ["rail_lines_km", "rail_freight_million_tkm", "rail_passengers_million_km",
                "gdp_per_capita_usd", "lpi_infrastructure_score"]:
        base = base.merge(latest(col), on="country_code", how="left")
    base["latest_freight_density_mtkm_per_km"] = (
        base["rail_freight_million_tkm"] / base["rail_lines_km"]).round(4)
    base["has_rail_network"] = base["rail_lines_km"].notna().astype(int)
    return base.sort_values(["region", "country_name"]).reset_index(drop=True)


def transform(config: Dict) -> Dict[str, Path]:
    """Run the full transformation stage and return the output paths."""
    paths = config["paths"]
    raw_dir = PROJECT_ROOT / paths["raw_dir"]
    master_path = PROJECT_ROOT / paths["master_file"]
    profiles_path = PROJECT_ROOT / paths["country_profiles_file"]

    frames = read_raw(config, raw_dir)
    if all(f.empty for f in frames.values()):
        raise RuntimeError(f"No raw data found in {raw_dir}; run the ingestion stage first")
    master = enrich(merge_series(frames), region_lookup(config),
                    int(config["pipeline"]["ffill_limit_years"]))
    profiles = build_country_profiles(master)

    master_path.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(master_path, index=False)
    profiles.to_csv(profiles_path, index=False)
    logger.info("Master table: %d rows x %d cols, %d countries -> %s",
                len(master), master.shape[1], master["country_code"].nunique(), master_path)
    logger.info("Country profiles: %d rows -> %s", len(profiles), profiles_path)
    if "US" not in set(master["country_code"]):
        logger.warning("USA benchmark rows are absent from the raw extract; "
                       "re-run ingestion to add them.")
    return {"master": master_path, "profiles": profiles_path}


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description="Clean and merge raw railway data.")
    parser.add_argument("--config", help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")).upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        transform(config)
    except (RuntimeError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
