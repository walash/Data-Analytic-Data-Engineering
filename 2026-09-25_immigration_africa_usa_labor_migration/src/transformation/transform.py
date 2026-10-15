"""Transformation module for the Africa-to-USA Labor Migration pipeline.

This module reads the six raw World Bank CSVs from ``data/raw/``, cleans them
(dropping null values, casting types, deduplicating), merges them into a single
master analytical dataset keyed by country and year, computes derived metrics
(remittance per capita, migration rate per 1,000 population), and writes the
result to ``data/processed/master_labor_migration.csv``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

import pandas as pd
import yaml

LOGGER = logging.getLogger("transformation")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.yaml")


def configure_logging(level: str = "INFO") -> None:
    """Configure logging with a consistent, timestamped format.

    Args:
        level: Logging level name.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load and parse the YAML configuration file.

    Args:
        config_path: Path to ``config.yaml``.

    Returns:
        Parsed configuration dictionary.
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_indicator(raw_dir: str, filename: str, value_name: str) -> pd.DataFrame:
    """Read a single raw indicator CSV and reshape it to a tidy frame.

    Cleans the frame by dropping rows with null values, casting ``year`` to
    integer and ``value`` to float, and deduplicating on country/year.

    Args:
        raw_dir: Directory containing raw CSV files.
        filename: CSV filename to read.
        value_name: Column name to assign to the indicator's value column.

    Returns:
        A DataFrame with columns ``country_id``, ``country_name``, ``year`` and
        ``value_name``.
    """
    path = os.path.join(raw_dir, filename)
    frame = pd.read_csv(path)
    frame = frame[["country_id", "country_name", "year", "value"]].copy()
    frame = frame.dropna(subset=["value"])
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["year", "value"])
    frame["year"] = frame["year"].astype(int)
    frame = frame.drop_duplicates(subset=["country_id", "year"], keep="first")
    frame = frame.rename(columns={"value": value_name})
    LOGGER.info("Loaded %s: %d clean rows", filename, len(frame))
    return frame


def build_countries_dimension(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build a distinct country dimension table from all indicator frames.

    Args:
        frames: Mapping of indicator name -> cleaned DataFrame.

    Returns:
        A DataFrame with unique ``country_id`` and ``country_name`` pairs.
    """
    parts = [f[["country_id", "country_name"]] for f in frames.values()]
    countries = pd.concat(parts, ignore_index=True).drop_duplicates()
    countries = countries.sort_values("country_id").reset_index(drop=True)
    return countries


def merge_datasets(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Merge all indicator frames into one master frame on country and year.

    Args:
        frames: Mapping of indicator name -> cleaned DataFrame. Each frame's
            value column is already named after its indicator.

    Returns:
        A merged DataFrame keyed by ``country_id``, ``country_name`` and ``year``.
    """
    order = [
        "net_migration",
        "remittances_usd",
        "remittances_pct_gdp",
        "gdp_per_capita",
        "unemployment",
        "population",
    ]
    master: pd.DataFrame | None = None
    for name in order:
        frame = frames[name]
        if master is None:
            master = frame
        else:
            master = master.merge(
                frame,
                on=["country_id", "country_name", "year"],
                how="outer",
            )
    assert master is not None
    master = master.sort_values(["country_id", "year"]).reset_index(drop=True)
    return master


def compute_derived_metrics(master: pd.DataFrame) -> pd.DataFrame:
    """Compute derived analytical metrics on the master frame.

    Derived metrics:
        * ``remittance_per_capita`` = remittances_usd / population
        * ``migration_rate_per_1000`` = net_migration / population * 1000

    Args:
        master: Merged master DataFrame.

    Returns:
        The master DataFrame with derived metric columns appended.
    """
    master = master.copy()
    master["remittance_per_capita"] = (
        master["remittances_usd"] / master["population"]
    ).round(2)
    master["migration_rate_per_1000"] = (
        master["net_migration"] / master["population"] * 1000
    ).round(3)
    return master


def transform(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Run the full transformation stage.

    Reads all raw indicators, cleans and merges them, computes derived metrics,
    and writes the master dataset to the processed directory.

    Args:
        config_path: Path to ``config.yaml``.

    Returns:
        Absolute path to the written master CSV file.
    """
    config = load_config(config_path)
    configure_logging(config["pipeline"].get("log_level", "INFO"))

    raw_dir = os.path.join(PROJECT_ROOT, config["paths"]["raw_data_dir"])
    processed_dir = os.path.join(PROJECT_ROOT, config["paths"]["processed_data_dir"])
    os.makedirs(processed_dir, exist_ok=True)

    indicators = config["api"]["indicators"]
    frames = {
        name: read_indicator(raw_dir, meta["filename"], name)
        for name, meta in indicators.items()
    }

    master = merge_datasets(frames)
    master = compute_derived_metrics(master)

    output_path = os.path.join(processed_dir, config["paths"]["processed_master_file"])
    master.to_csv(output_path, index=False)
    LOGGER.info("Wrote master dataset (%d rows) to %s", len(master), output_path)
    return output_path


def main() -> None:
    """Command-line entry point for the transformation stage."""
    transform()


if __name__ == "__main__":
    main()
