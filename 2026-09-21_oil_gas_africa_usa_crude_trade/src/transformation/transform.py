"""Transformation stage for the Oil & Gas Africa-USA project.

Reads the seven raw World Bank JSON files from ``data/raw/``, normalises each
into a tidy long-format DataFrame, then pivots and merges them into a single
wide-format master table with derived analytical columns.

Outputs:
    * ``data/processed/oil_gas_master.csv``
    * ``data/processed/oil_gas_master.parquet``

Run directly with::

    python src/transformation/transform.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed"

# Map raw file stem -> the wide-format column name it becomes.
INDICATOR_FILES: Dict[str, str] = {
    "wb_oil_rents": "oil_rents_pct_gdp",
    "wb_natural_resources_rents": "natural_resources_rents_pct_gdp",
    "wb_gdp_per_capita": "gdp_per_capita_usd",
    "wb_energy_use": "energy_use_kg_oil_eq",
    "wb_electricity_from_oil": "electricity_from_oil_pct",
    "wb_merchandise_exports": "merchandise_exports_usd",
    "wb_fdi_inflows": "fdi_inflows_usd",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("transform")


def parse_worldbank_json(file_stem: str) -> pd.DataFrame:
    """Parse one World Bank raw JSON file into a tidy long DataFrame.

    Args:
        file_stem: File stem under ``data/raw/`` (without extension).

    Returns:
        DataFrame with columns:
        ``country_name, iso3, year, indicator_name, value``.
    """
    path = RAW_DATA_DIR / f"{file_stem}.json"
    if not path.exists():
        logger.warning("Missing raw file: %s", path)
        return pd.DataFrame(
            columns=["country_name", "iso3", "year", "indicator_name", "value"]
        )

    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    # World Bank format: [metadata, records]
    records = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
    rows = []
    for rec in records:
        rows.append(
            {
                "country_name": (rec.get("country") or {}).get("value"),
                "iso3": rec.get("countryiso3code"),
                "year": rec.get("date"),
                "indicator_name": (rec.get("indicator") or {}).get("value"),
                "value": rec.get("value"),
            }
        )
    df = pd.DataFrame(rows)
    logger.info("Parsed %s: %d raw rows", file_stem, len(df))
    return df


def clean_long_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a tidy long DataFrame: drop nulls, cast types, deduplicate.

    Args:
        df: Long DataFrame from :func:`parse_worldbank_json`.

    Returns:
        Cleaned long DataFrame.
    """
    if df.empty:
        return df

    # Drop rows with no numeric value.
    df = df[df["value"].notna()].copy()

    # Type casting.
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[df["year"].notna() & df["value"].notna()].copy()
    df["year"] = df["year"].astype(int)
    df["value"] = df["value"].astype(float)

    # Deduplicate on (iso3, year), keeping the last occurrence.
    before = len(df)
    df = df.drop_duplicates(subset=["iso3", "year"], keep="last")
    if before != len(df):
        logger.info("Dropped %d duplicate (iso3, year) rows", before - len(df))

    return df


def build_master(long_frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Pivot each indicator to wide format and merge into a master table.

    Args:
        long_frames: Mapping of wide-column-name -> cleaned long DataFrame.

    Returns:
        Wide-format master DataFrame with derived columns.
    """
    master: pd.DataFrame | None = None

    for column_name, frame in long_frames.items():
        if frame.empty:
            logger.warning("No data for %s; skipping merge", column_name)
            continue
        wide = frame[["iso3", "country_name", "year", "value"]].rename(
            columns={"value": column_name}
        )
        if master is None:
            master = wide
        else:
            # Merge on identity keys; country_name kept from first frame.
            master = master.merge(
                wide.drop(columns=["country_name"]),
                on=["iso3", "year"],
                how="outer",
            )

    if master is None:
        raise RuntimeError("No indicator data available to build master table.")

    # Ensure all expected value columns exist even if a source was missing.
    for column_name in INDICATOR_FILES.values():
        if column_name not in master.columns:
            master[column_name] = np.nan

    # Derived column: oil dependency ratio (safe division).
    denom = master["natural_resources_rents_pct_gdp"].replace(0, np.nan)
    master["oil_dependency_ratio"] = master["oil_rents_pct_gdp"] / denom

    # Derived column: African flag (USA is the only non-African country).
    master["is_african"] = (master["iso3"] != "USA").astype(int)

    # Order columns.
    ordered = [
        "iso3",
        "country_name",
        "year",
        "oil_rents_pct_gdp",
        "natural_resources_rents_pct_gdp",
        "gdp_per_capita_usd",
        "energy_use_kg_oil_eq",
        "electricity_from_oil_pct",
        "merchandise_exports_usd",
        "fdi_inflows_usd",
        "oil_dependency_ratio",
        "is_african",
    ]
    master = master[[c for c in ordered if c in master.columns]]
    master = master.sort_values(["iso3", "year"]).reset_index(drop=True)
    return master


def save_master(master: pd.DataFrame) -> None:
    """Persist the master table to CSV and Parquet.

    Args:
        master: Wide-format master DataFrame.
    """
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = PROCESSED_DATA_DIR / "oil_gas_master.csv"
    parquet_path = PROCESSED_DATA_DIR / "oil_gas_master.parquet"

    master.to_csv(csv_path, index=False)
    logger.info("Wrote %s (%d rows)", csv_path, len(master))

    try:
        master.to_parquet(parquet_path, index=False)
        logger.info("Wrote %s (%d rows)", parquet_path, len(master))
    except (ImportError, ValueError) as exc:
        logger.warning("Could not write Parquet (%s). CSV is available.", exc)


def transform() -> pd.DataFrame:
    """Run the full transformation pipeline.

    Returns:
        The wide-format master DataFrame.
    """
    logger.info("Starting transformation of %d indicators", len(INDICATOR_FILES))
    long_frames: Dict[str, pd.DataFrame] = {}
    for file_stem, column_name in INDICATOR_FILES.items():
        raw = parse_worldbank_json(file_stem)
        long_frames[column_name] = clean_long_frame(raw)

    master = build_master(long_frames)
    save_master(master)
    logger.info(
        "Transformation complete: %d rows, %d columns",
        len(master),
        len(master.columns),
    )
    return master


def main() -> None:
    """Entry point."""
    transform()


if __name__ == "__main__":
    main()
