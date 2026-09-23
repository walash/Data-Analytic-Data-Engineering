"""Transformation module for the Africa-USA Maritime Port Trade project.

This module reads the five raw indicator CSVs produced by the ingestion stage,
cleans and standardises each one (null handling, type casting, deduplication),
merges them into a single master DataFrame keyed on ``(country, iso3, year)``
and computes derived analytical metrics. The cleaned individual files and the
merged master file are written to ``data/processed/``.

Derived metrics:
    * shipping_trade_ratio  = liner_shipping_index / merchandise_trade_pct_gdp
    * port_efficiency_score = container_throughput_teu / lpi_score

Usage:
    python src/transformation/transform.py
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("transform")

# Raw filename -> value column name.
RAW_FILES: Dict[str, str] = {
    "liner_shipping_connectivity.csv": "liner_shipping_index",
    "container_port_throughput.csv": "container_throughput_teu",
    "merchandise_trade_pct_gdp.csv": "merchandise_trade_pct_gdp",
    "logistics_performance_index.csv": "lpi_score",
    "exports_goods_services_usd.csv": "exports_usd",
}

# African countries covered (everything except the USA is treated as Africa).
AFRICAN_ISO3 = {
    "DZA", "AGO", "CMR", "CIV", "EGY", "ETH", "GHA", "KEN", "LBY",
    "MDG", "MAR", "MOZ", "NGA", "SEN", "ZAF", "TZA", "TUN",
}

# Sub-region mapping used for regional aggregation downstream.
SUBREGION_MAP = {
    "DZA": "North Africa", "EGY": "North Africa", "LBY": "North Africa",
    "MAR": "North Africa", "TUN": "North Africa",
    "AGO": "Central Africa", "CMR": "Central Africa",
    "CIV": "West Africa", "GHA": "West Africa", "NGA": "West Africa",
    "SEN": "West Africa",
    "ETH": "East Africa", "KEN": "East Africa", "MDG": "East Africa",
    "MOZ": "East Africa", "TZA": "East Africa",
    "ZAF": "Southern Africa",
    "USA": "North America",
}


def _project_root() -> str:
    """Return the absolute path to the project root directory.

    Returns:
        str: Absolute path of the project root (two levels up from this file).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def load_config() -> Optional[dict]:
    """Load ``config/config.yaml`` when available.

    Returns:
        Optional[dict]: Parsed configuration, or ``None`` if unavailable.
    """
    config_path = os.path.join(_project_root(), "config", "config.yaml")
    if yaml is None or not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_paths(config: Optional[dict]) -> Dict[str, str]:
    """Resolve raw and processed data directories from config (or defaults).

    Args:
        config: Optional configuration dictionary.

    Returns:
        Dict[str, str]: Mapping with ``raw`` and ``processed`` absolute paths.
        The processed directory is created if it does not exist.
    """
    raw_rel, proc_rel = "data/raw", "data/processed"
    if config:
        paths = config.get("paths", {})
        raw_rel = paths.get("raw_data_dir", raw_rel)
        proc_rel = paths.get("processed_data_dir", proc_rel)
    root = _project_root()
    raw = os.path.join(root, raw_rel)
    processed = os.path.join(root, proc_rel)
    os.makedirs(processed, exist_ok=True)
    return {"raw": raw, "processed": processed}


def clean_indicator(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    """Clean a single indicator DataFrame.

    Performs null handling, type casting (year -> int, value -> float),
    trimming of country names and deduplication on ``(iso3, year)`` keeping the
    last observation.

    Args:
        frame: Raw indicator DataFrame with columns
            ``country, iso3, year, <value_column>``.
        value_column: Name of the numeric value column.

    Returns:
        pd.DataFrame: Cleaned DataFrame with proper dtypes and no duplicates.
    """
    df = frame.copy()
    # Standardise column presence.
    expected = ["country", "iso3", "year", value_column]
    df = df[[c for c in expected if c in df.columns]]

    # Trim whitespace on text columns.
    for col in ("country", "iso3"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()

    # Type casting with coercion.
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")

    # Drop rows missing key fields or the value.
    df = df.dropna(subset=["iso3", "year", value_column])
    df["year"] = df["year"].astype(int)
    df[value_column] = df[value_column].astype(float)

    # Deduplicate on country-year, keeping the last (most complete) record.
    df = df.drop_duplicates(subset=["iso3", "year"], keep="last")

    df = df.sort_values(["iso3", "year"]).reset_index(drop=True)
    return df


def load_and_clean_all(raw_dir: str) -> Dict[str, pd.DataFrame]:
    """Load and clean every raw indicator CSV.

    Args:
        raw_dir: Absolute path to the raw data directory.

    Returns:
        Dict[str, pd.DataFrame]: Mapping of value-column name -> cleaned frame.

    Raises:
        FileNotFoundError: If a required raw CSV is missing.
    """
    cleaned: Dict[str, pd.DataFrame] = {}
    for filename, value_column in RAW_FILES.items():
        path = os.path.join(raw_dir, filename)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Required raw file not found: {path}")
        raw = pd.read_csv(path)
        LOGGER.info("Loaded %s (%d rows)", filename, len(raw))
        cleaned[value_column] = clean_indicator(raw, value_column)
    return cleaned


def merge_indicators(cleaned: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge all cleaned indicators into a single master DataFrame.

    The merge is an outer join on ``(country, iso3, year)`` so that a country /
    year present in any indicator appears in the master frame.

    Args:
        cleaned: Mapping of value-column name -> cleaned DataFrame.

    Returns:
        pd.DataFrame: Master DataFrame with one row per ``(iso3, year)`` and one
        column per indicator, plus a ``region`` and ``sub_region`` column.
    """
    master: Optional[pd.DataFrame] = None
    for value_column, frame in cleaned.items():
        subset = frame[["country", "iso3", "year", value_column]]
        if master is None:
            master = subset.copy()
        else:
            master = master.merge(
                subset, on=["iso3", "year"], how="outer",
                suffixes=("", "_dup"),
            )
            # Consolidate country name if the base merge produced a NaN.
            if "country_dup" in master.columns:
                master["country"] = master["country"].fillna(
                    master["country_dup"]
                )
                master = master.drop(columns=["country_dup"])

    assert master is not None, "No indicators to merge."

    master["region"] = np.where(
        master["iso3"].isin(AFRICAN_ISO3), "Africa",
        np.where(master["iso3"] == "USA", "USA", "Other"),
    )
    master["sub_region"] = master["iso3"].map(SUBREGION_MAP).fillna("Unknown")

    master = master.sort_values(["iso3", "year"]).reset_index(drop=True)
    return master


def compute_derived_metrics(master: pd.DataFrame) -> pd.DataFrame:
    """Compute derived analytical metrics on the master DataFrame.

    Adds:
        * ``shipping_trade_ratio``  = liner_shipping_index / merchandise_trade_pct_gdp
        * ``port_efficiency_score`` = container_throughput_teu / lpi_score

    Division-by-zero and missing operands yield NaN (not infinity).

    Args:
        master: Merged master DataFrame.

    Returns:
        pd.DataFrame: The master DataFrame with the two derived columns added.
    """
    df = master.copy()

    def _safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
        """Element-wise division that returns NaN on zero/invalid denominators."""
        denom = denominator.replace({0: np.nan})
        result = numerator / denom
        return result.replace([np.inf, -np.inf], np.nan)

    if {"liner_shipping_index", "merchandise_trade_pct_gdp"} <= set(df.columns):
        df["shipping_trade_ratio"] = _safe_divide(
            df["liner_shipping_index"], df["merchandise_trade_pct_gdp"]
        )
    else:
        df["shipping_trade_ratio"] = np.nan

    if {"container_throughput_teu", "lpi_score"} <= set(df.columns):
        df["port_efficiency_score"] = _safe_divide(
            df["container_throughput_teu"], df["lpi_score"]
        )
    else:
        df["port_efficiency_score"] = np.nan

    return df


def write_outputs(
    cleaned: Dict[str, pd.DataFrame],
    master: pd.DataFrame,
    processed_dir: str,
) -> List[str]:
    """Write cleaned individual CSVs and the master CSV to disk.

    Args:
        cleaned: Mapping of value-column name -> cleaned DataFrame.
        master: Master DataFrame with derived metrics.
        processed_dir: Absolute path to the processed data directory.

    Returns:
        List[str]: Absolute paths of all files written.
    """
    written: List[str] = []
    for value_column, frame in cleaned.items():
        out = os.path.join(processed_dir, f"clean_{value_column}.csv")
        frame.to_csv(out, index=False)
        written.append(out)
        LOGGER.info("Wrote cleaned indicator -> %s (%d rows)", out, len(frame))

    master_path = os.path.join(processed_dir, "master_maritime_data.csv")
    master.to_csv(master_path, index=False)
    written.append(master_path)
    LOGGER.info("Wrote master dataset -> %s (%d rows)", master_path, len(master))
    return written


def transform() -> pd.DataFrame:
    """Run the full transformation stage end-to-end.

    Returns:
        pd.DataFrame: The final master DataFrame (also persisted to disk).
    """
    config = load_config()
    paths = resolve_paths(config)

    cleaned = load_and_clean_all(paths["raw"])
    master = merge_indicators(cleaned)
    master = compute_derived_metrics(master)
    write_outputs(cleaned, master, paths["processed"])

    LOGGER.info(
        "Transformation complete: %d rows, %d columns.",
        master.shape[0], master.shape[1],
    )
    return master


def main() -> None:
    """Command-line entry point for the transformation stage."""
    LOGGER.info("Starting transformation stage ...")
    transform()
    LOGGER.info("Transformation stage finished.")


if __name__ == "__main__":
    main()
