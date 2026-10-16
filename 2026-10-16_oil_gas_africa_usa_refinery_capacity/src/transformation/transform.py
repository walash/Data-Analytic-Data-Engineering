"""
Transformation stage - Oil & Gas Refinery Capacity & Fuel Access (Africa vs USA).

Reads the six raw World Bank indicator CSVs from ``data/raw/``, cleans and
normalises them, merges them into one country-year panel and derives
refining-gap / fuel-access metrics.

Outputs (``data/processed/``)
-----------------------------
* ``master_dataset.csv``          - balanced country x year panel (2012-2025)
* ``country_latest_snapshot.csv`` - latest non-null value of every indicator
* ``data_quality_report.csv``     - coverage / null / imputation statistics

Cleaning rules
--------------
1. Standardise column names, strip whitespace, upper-case ISO codes.
2. Cast ``year`` to int and indicator values to float (invalid -> NaN).
3. Drop rows with missing keys, restrict to configured years and countries.
4. De-duplicate on (iso3, year), keeping the last (most recently revised) row.
5. Treat exact ``0.0`` in ``fossil_fuel_pct_energy`` as missing - the World
   Bank series EG.USE.COMM.FO.ZS stopped updating after 2015 and the API
   returns zero-filled placeholders for later years.
6. Clip percentage indicators to [0, 100] and GDP per capita to > 0.
7. Linearly interpolate *interior* gaps only (no extrapolation), limited to
   ``pipeline.interpolation_limit`` consecutive years; every imputed cell is
   flagged in a ``<column>_imputed`` boolean column.

Usage
-----
    python src/transformation/transform.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("transform")

PERCENT_COLUMNS = [
    "oil_rents_pct_gdp",
    "fuel_exports_pct_merchandise",
    "fuel_imports_pct_merchandise",
    "electricity_access_pct",
    "fossil_fuel_pct_energy",
]
COUNTRY_NAME_FIXES = {
    "Congo, Dem. Rep.": "DR Congo",
    "Egypt, Arab Rep.": "Egypt",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent, timestamped format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration (``$CONFIG_PATH`` or ``config/config.yaml``)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_indicator(path: Path, value_column: str) -> pd.DataFrame:
    """Read and clean a single raw indicator CSV.

    Args:
        path: CSV path in ``data/raw``.
        value_column: Expected name of the indicator value column.

    Returns:
        DataFrame with columns ``iso3, country, year, <value_column>``,
        typed, de-duplicated and free of null keys.

    Raises:
        FileNotFoundError: If the CSV is missing.
        ValueError: If required columns are absent.
    """
    if not path.exists():
        raise FileNotFoundError(f"Raw file not found: {path}")
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    required = {"country", "iso3", "year", value_column}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")

    n_raw = len(df)
    df = df[list(required)].copy()
    df["iso3"] = df["iso3"].astype(str).str.strip().str.upper()
    df["country"] = df["country"].astype(str).str.strip().replace(COUNTRY_NAME_FIXES)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")
    df = df.dropna(subset=["iso3", "year"])
    df = df[df["iso3"].str.len() == 3]
    df["year"] = df["year"].astype(int)
    before = len(df)
    df = df.drop_duplicates(subset=["iso3", "year"], keep="last")
    logger.info("%-30s raw=%4d clean=%4d dupes_removed=%d nulls=%d",
                path.name, n_raw, len(df), before - len(df), int(df[value_column].isna().sum()))
    return df[["iso3", "country", "year", value_column]]


def apply_value_rules(df: pd.DataFrame, zero_as_missing: List[str]) -> pd.DataFrame:
    """Apply domain validation rules to indicator columns.

    Args:
        df: Merged panel.
        zero_as_missing: Columns where exact zeros are API fill values.

    Returns:
        Panel with placeholder zeros nulled and values clipped to valid ranges.
    """
    df = df.copy()
    for col in zero_as_missing:
        if col in df:
            n_zero = int((df[col] == 0).sum())
            df.loc[df[col] == 0, col] = np.nan
            logger.info("Converted %d placeholder zeros to NaN in %s", n_zero, col)
    for col in PERCENT_COLUMNS:
        if col in df:
            df[col] = df[col].clip(lower=0, upper=100)
    if "gdp_per_capita_usd" in df:
        df.loc[df["gdp_per_capita_usd"] <= 0, "gdp_per_capita_usd"] = np.nan
    return df


def interpolate_interior(df: pd.DataFrame, columns: List[str], limit: int) -> pd.DataFrame:
    """Fill short interior gaps per country by linear interpolation.

    Only gaps bounded by real observations on both sides are filled
    (``limit_area='inside'``), so no value is ever extrapolated beyond the
    last published year. An ``<column>_imputed`` flag is added per column.

    Args:
        df: Balanced panel sorted by iso3/year.
        columns: Indicator columns to interpolate.
        limit: Maximum consecutive years to fill.

    Returns:
        Panel with interpolated values and imputation flags.
    """
    df = df.sort_values(["iso3", "year"]).copy()
    for col in columns:
        was_null = df[col].isna()
        df[col] = df.groupby("iso3")[col].transform(
            lambda s: s.interpolate(method="linear", limit=limit, limit_area="inside")
        )
        df[f"{col}_imputed"] = was_null & df[col].notna()
        logger.info("Imputed %d interior gaps in %s", int(df[f"{col}_imputed"].sum()), col)
    return df


def classify_archetype(row: pd.Series, t: Dict[str, float]) -> str:
    """Assign a fuel-trade archetype to a country-year.

    * ``Petro-exporter with refining gap``: fuel dominates exports AND refined
      fuel is a large share of imports - the classic crude-out / products-in
      pattern caused by insufficient domestic refinery capacity.
    * ``Petro-exporter``: fuel-dominated exports without a heavy import bill.
    * ``Net fuel importer``: fuel imports materially exceed fuel exports.
    * ``Diversified``: neither fuel exports nor imports dominate trade.
    * ``Insufficient data``: trade indicators unavailable.
    """
    exp_, imp_ = row.get("fuel_exports_pct_merchandise"), row.get("fuel_imports_pct_merchandise")
    if pd.isna(exp_) or pd.isna(imp_):
        return "Insufficient data"
    if exp_ >= t["petro_state_fuel_exports_pct"]:
        if imp_ >= t["high_fuel_import_pct"]:
            return "Petro-exporter with refining gap"
        return "Petro-exporter"
    if imp_ - exp_ >= 5:
        return "Net fuel importer"
    return "Diversified"


def add_derived_metrics(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Add analytical features used by SQL queries and the notebook.

    Args:
        df: Cleaned, merged panel.
        config: Full configuration (for thresholds and region metadata).

    Returns:
        Panel enriched with region metadata and derived metrics.
    """
    t = config["pipeline"]["thresholds"]
    regions = pd.DataFrame.from_dict(config["regions"], orient="index")
    regions.index.name = "iso3"
    df = df.merge(regions.reset_index(), on="iso3", how="left")

    exp_, imp_ = df["fuel_exports_pct_merchandise"], df["fuel_imports_pct_merchandise"]
    df["net_fuel_trade_pp"] = (exp_ - imp_).round(3)
    df["fuel_import_export_ratio"] = np.where(exp_ > 0, (imp_ / exp_).round(4), np.nan)
    df["electricity_access_gap_pct"] = (100 - df["electricity_access_pct"]).round(3)

    producer = (df["oil_rents_pct_gdp"] >= t["high_oil_rents_pct_gdp"]) | \
               (exp_ >= t["petro_state_fuel_exports_pct"])
    df["refining_gap_flag"] = (producer & (imp_ >= t["high_fuel_import_pct"])).astype(int)
    df["energy_poverty_flag"] = (df["electricity_access_pct"] < t["energy_poverty_access_pct"]) \
        .astype(int)
    df["fuel_trade_archetype"] = df.apply(classify_archetype, axis=1, t=t)

    usa_gdp = df.loc[df["iso3"] == "USA", ["year", "gdp_per_capita_usd"]] \
        .rename(columns={"gdp_per_capita_usd": "usa_gdp_per_capita_usd"})
    df = df.merge(usa_gdp, on="year", how="left")
    df["gdp_pc_pct_of_usa"] = (100 * df["gdp_per_capita_usd"] / df["usa_gdp_per_capita_usd"]) \
        .round(3)
    df = df.drop(columns="usa_gdp_per_capita_usd")

    df = df.sort_values(["iso3", "year"])
    df["oil_rents_yoy_pp"] = df.groupby("iso3")["oil_rents_pct_gdp"].diff().round(4)
    df["electricity_access_yoy_pp"] = df.groupby("iso3")["electricity_access_pct"].diff().round(4)
    return df


def build_master(config: Dict[str, Any]) -> pd.DataFrame:
    """Read, clean and merge all six indicators into the master panel.

    Args:
        config: Full configuration dictionary.

    Returns:
        Balanced country x year master DataFrame.
    """
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    pipe = config["pipeline"]
    indicators = config["worldbank"]["indicators"]
    valid_iso3 = set(config["regions"])

    frames, names = [], []
    for spec in indicators.values():
        df = read_indicator(raw_dir / spec["file"], spec["value_column"])
        df = df[df["iso3"].isin(valid_iso3)]
        df = df[df["year"].between(pipe["year_min"], pipe["year_max"])]
        names.append(df[["iso3", "country"]])
        frames.append(df.drop(columns="country"))

    country_names = pd.concat(names).drop_duplicates("iso3", keep="last")
    years = range(int(pipe["year_min"]), int(pipe["year_max"]) + 1)
    grid = pd.MultiIndex.from_product([sorted(valid_iso3), years], names=["iso3", "year"]) \
        .to_frame(index=False)
    master = grid
    for frame in frames:
        master = master.merge(frame, on=["iso3", "year"], how="left", validate="one_to_one")
    master = master.merge(country_names, on="iso3", how="left")

    value_cols = [s["value_column"] for s in indicators.values()]
    master = apply_value_rules(master, pipe.get("zero_as_missing", []))
    master = interpolate_interior(master, value_cols, int(pipe.get("interpolation_limit", 2)))
    master = master.dropna(subset=value_cols, how="all")
    master = add_derived_metrics(master, config)

    front = ["iso3", "iso2", "country", "region", "subregion", "opec_member", "year"]
    master = master[front + [c for c in master.columns if c not in front]]
    master = master.sort_values(["region", "country", "year"]).reset_index(drop=True)
    return master


def build_latest_snapshot(master: pd.DataFrame, value_cols: List[str]) -> pd.DataFrame:
    """Return the latest non-null observation (and its year) per indicator.

    Args:
        master: Master panel.
        value_cols: Indicator columns.

    Returns:
        One row per country with ``<col>`` and ``<col>_year`` columns.
    """
    base = master[["iso3", "iso2", "country", "region", "subregion", "opec_member"]] \
        .drop_duplicates("iso3").set_index("iso3")
    for col in value_cols:
        latest = master.dropna(subset=[col]).sort_values("year").groupby("iso3").last()
        base[col] = latest[col]
        base[f"{col}_year"] = latest["year"].astype("Int64")
    return base.reset_index().sort_values(["region", "country"])


def build_quality_report(master: pd.DataFrame, value_cols: List[str]) -> pd.DataFrame:
    """Summarise coverage, null rate and imputation count per indicator."""
    rows = []
    for col in value_cols:
        s = master[col]
        rows.append({
            "indicator": col,
            "rows": len(s),
            "non_null": int(s.notna().sum()),
            "null_pct": round(100 * s.isna().mean(), 2),
            "imputed": int(master[f"{col}_imputed"].sum()),
            "countries_covered": int(master.loc[s.notna(), "iso3"].nunique()),
            "min_year": int(master.loc[s.notna(), "year"].min()) if s.notna().any() else None,
            "max_year": int(master.loc[s.notna(), "year"].max()) if s.notna().any() else None,
            "min": round(float(s.min()), 4) if s.notna().any() else None,
            "max": round(float(s.max()), 4) if s.notna().any() else None,
            "mean": round(float(s.mean()), 4) if s.notna().any() else None,
        })
    return pd.DataFrame(rows)


def transform(config: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Run the transformation stage and write processed outputs.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        Mapping of output file -> row count.
    """
    config = config or load_config()
    paths = config["paths"]
    value_cols = [s["value_column"] for s in config["worldbank"]["indicators"].values()]
    (PROJECT_ROOT / paths["processed_dir"]).mkdir(parents=True, exist_ok=True)

    master = build_master(config)
    snapshot = build_latest_snapshot(master, value_cols)
    quality = build_quality_report(master, value_cols)

    outputs = {
        paths["master_dataset"]: master,
        paths["latest_snapshot"]: snapshot,
        paths["data_quality_report"]: quality,
    }
    summary = {}
    for rel_path, frame in outputs.items():
        frame.to_csv(PROJECT_ROOT / rel_path, index=False)
        summary[rel_path] = len(frame)
        logger.info("Wrote %d rows -> %s", len(frame), rel_path)
    logger.info("Master: %d countries x %d-%d, %d columns",
                master["iso3"].nunique(), master["year"].min(), master["year"].max(),
                master.shape[1])
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Transform raw oil & gas indicators.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        setup_logging(config["pipeline"].get("log_level", "INFO"))
        transform(config)
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit code 1
        setup_logging()
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
