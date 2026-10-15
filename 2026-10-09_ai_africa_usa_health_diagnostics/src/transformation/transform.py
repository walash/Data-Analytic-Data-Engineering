"""Transformation stage for the AI Healthcare Diagnostics (Africa & USA) pipeline.

Reads the five raw World Bank JSON files from ``data/raw/``, parses the
``[metadata, records]`` structure, and produces one tidy wide-format table:

    country_code, country_name, year, health_expenditure_pc,
    physicians_per_1000, under5_mortality, hospital_beds_per_1000,
    life_expectancy, africa_flag, healthcare_access_score

Cleaning steps:
    * type casting (year -> int, indicators -> float)
    * deduplication on (country_code, year)
    * forward-fill of missing values within each country (sorted by year),
      so sparse series such as physicians and hospital beds carry their most
      recent observed value forward
    * derived ``africa_flag`` (1 = African country, 0 = United States)
    * derived ``healthcare_access_score`` (0-100 composite of physicians,
      hospital beds and health expenditure, min-max normalised)

Output: ``data/processed/health_diagnostics_processed.csv``

Usage:
    python src/transformation/transform.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

# raw file stem -> processed column
INDICATOR_FILES = {
    "health_expenditure_per_capita": "health_expenditure_pc",
    "physicians_per_1000": "physicians_per_1000",
    "under5_mortality": "under5_mortality",
    "hospital_beds_per_1000": "hospital_beds_per_1000",
    "life_expectancy": "life_expectancy",
}
INDICATOR_COLUMNS = list(INDICATOR_FILES.values())
ACCESS_COMPONENTS = ["physicians_per_1000", "hospital_beds_per_1000", "health_expenditure_pc"]
OUTPUT_COLUMNS = [
    "country_code", "country_name", "year", *INDICATOR_COLUMNS,
    "africa_flag", "healthcare_access_score",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | transform | %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load YAML configuration; return an empty dict if it is missing."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def parse_worldbank_json(path: Path, column: str) -> pd.DataFrame:
    """Parse one World Bank API JSON file into a long DataFrame.

    Args:
        path: Raw JSON file (``[metadata, records]``).
        column: Name to give the indicator value column.

    Returns:
        DataFrame with ``country_code, country_name, year, <column>``.
        ``country_code`` is the ISO-2 code from ``country.id``.
    """
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        logger.warning("No records in %s", path.name)
        return pd.DataFrame(columns=["country_code", "country_name", "year", column])

    rows = [
        {
            "country_code": rec["country"]["id"],
            "country_iso3": rec.get("countryiso3code"),
            "country_name": rec["country"]["value"],
            "year": rec["date"],
            column: rec["value"],
        }
        for rec in payload[1]
    ]
    frame = pd.DataFrame(rows)
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
    frame = frame.dropna(subset=["year"]).drop(columns=["country_iso3"])
    logger.info("Parsed %s: %d rows (%d non-null)", path.name, len(frame), frame[column].notna().sum())
    return frame


def build_wide_table(raw_dir: Path) -> pd.DataFrame:
    """Merge all indicator files into one wide table keyed on country/year."""
    wide: pd.DataFrame | None = None
    for stem, column in INDICATOR_FILES.items():
        path = raw_dir / f"{stem}.json"
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file: {path}. Run ingest.py first.")
        frame = parse_worldbank_json(path, column)
        frame = frame.drop_duplicates(subset=["country_code", "year"], keep="last")
        if wide is None:
            wide = frame
        else:
            wide = wide.merge(frame, on=["country_code", "year"], how="outer", suffixes=("", "_dup"))
            wide["country_name"] = wide["country_name"].fillna(wide.pop("country_name_dup"))
    assert wide is not None
    return wide


def clean(frame: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate, cast types and forward-fill missing values per country."""
    frame = frame.drop_duplicates(subset=["country_code", "year"], keep="last").copy()
    frame["year"] = frame["year"].astype(int)
    frame = frame.sort_values(["country_code", "year"]).reset_index(drop=True)
    frame["country_name"] = frame.groupby("country_code")["country_name"].transform(
        lambda s: s.ffill().bfill()
    )
    before = int(frame[INDICATOR_COLUMNS].isna().sum().sum())
    frame[INDICATOR_COLUMNS] = frame.groupby("country_code")[INDICATOR_COLUMNS].ffill()
    after = int(frame[INDICATOR_COLUMNS].isna().sum().sum())
    logger.info("Forward-fill within countries: nulls %d -> %d", before, after)
    return frame


def min_max(series: pd.Series) -> pd.Series:
    """Scale a series to 0-1; constant or empty series map to 0."""
    lo, hi = series.min(), series.max()
    if pd.isna(lo) or hi == lo:
        return pd.Series(0.0, index=series.index)
    return (series - lo) / (hi - lo)


def add_derived_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``africa_flag`` and the 0-100 ``healthcare_access_score``.

    The access score averages the min-max normalised physicians, hospital
    beds and health expenditure values that are available for each row
    (missing components are ignored rather than counted as zero). Health
    expenditure is log-transformed first so the USA does not compress every
    African value towards zero.
    """
    frame = frame.copy()
    frame["africa_flag"] = (frame["country_code"] != "US").astype(int)
    components = pd.DataFrame(
        {
            "physicians_per_1000": min_max(frame["physicians_per_1000"]),
            "hospital_beds_per_1000": min_max(frame["hospital_beds_per_1000"]),
            "health_expenditure_pc": min_max(np.log1p(frame["health_expenditure_pc"])),
        }
    )
    frame["healthcare_access_score"] = (components[ACCESS_COMPONENTS].mean(axis=1, skipna=True) * 100).round(2)
    return frame


def transform() -> pd.DataFrame:
    """Run the full transformation and write the processed CSV.

    Returns:
        The processed DataFrame that was written to disk.
    """
    config = load_config()
    paths = config.get("paths", {})
    raw_dir = Path(os.getenv("RAW_DATA_PATH", PROJECT_ROOT / paths.get("raw", "data/raw")))
    out_path = Path(os.getenv(
        "PROCESSED_FILE",
        PROJECT_ROOT / paths.get("processed_file", "data/processed/health_diagnostics_processed.csv"),
    ))

    frame = add_derived_columns(clean(build_wide_table(raw_dir)))
    frame = frame[OUTPUT_COLUMNS]
    for col in INDICATOR_COLUMNS:
        frame[col] = frame[col].round(3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path, index=False)
    logger.info(
        "Wrote %s: %d rows, %d countries, years %d-%d",
        out_path, len(frame), frame["country_code"].nunique(),
        frame["year"].min(), frame["year"].max(),
    )
    return frame


def main() -> int:
    """CLI entry point."""
    try:
        transform()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        logger.critical("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
