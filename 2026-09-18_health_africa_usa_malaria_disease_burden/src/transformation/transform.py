"""Transformation module for the Malaria & Infectious Disease Burden pipeline.

Reads the five raw CSVs produced by :mod:`src.ingestion.ingest`, cleans and
enriches them, and writes two analysis-ready datasets to ``data/processed/``:

* ``malaria_master.csv`` — merged malaria incidence + deaths with a burden
  category and year-over-year incidence change.
* ``health_indicators.csv`` — merged health expenditure, life expectancy and
  under-5 mortality with a region label.

Cleaning steps: null handling, type casting (year -> int, numeric -> float) and
deduplication. Enrichment steps: region labels, ``malaria_burden_category``
(low/medium/high/very_high from incidence quartiles) and
``incidence_yoy_change``.

Run as a module::

    python -m src.transformation.transform
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("transform")

# ISO-3 codes for the ten African focus countries.
AFRICAN_FOCUS = {"KEN", "ETH", "NGA", "GHA", "ZAF", "UGA", "TZA", "MOZ", "ZMB", "MWI"}

# Human-readable names keyed by ISO-3 code (used to backfill WHO rows that carry
# only the code in the country_name column).
COUNTRY_NAMES = {
    "KEN": "Kenya",
    "ETH": "Ethiopia",
    "NGA": "Nigeria",
    "GHA": "Ghana",
    "ZAF": "South Africa",
    "UGA": "Uganda",
    "TZA": "Tanzania",
    "MOZ": "Mozambique",
    "ZMB": "Zambia",
    "MWI": "Malawi",
    "USA": "United States",
}


def _project_root() -> str:
    """Return the absolute path to the project root (two levels above this file)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_config(config_path: Optional[str] = None) -> dict:
    """Load the YAML configuration file.

    Args:
        config_path: Optional explicit path to ``config.yaml``.

    Returns:
        The parsed configuration dictionary.
    """
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    LOGGER.info("Loaded configuration from %s", config_path)
    return config


def resolve_paths(config: dict) -> Dict[str, str]:
    """Resolve absolute raw/processed directory paths.

    Args:
        config: The parsed configuration dictionary.

    Returns:
        A dictionary with ``raw`` and ``processed`` directory paths.
    """
    root = _project_root()
    raw_dir = os.path.join(root, config["paths"]["raw_data_dir"])
    processed_dir = os.path.join(root, config["paths"]["processed_data_dir"])
    os.makedirs(processed_dir, exist_ok=True)
    return {"raw": raw_dir, "processed": processed_dir}


def _read_csv(raw_dir: str, filename: str) -> pd.DataFrame:
    """Read a raw CSV file into a DataFrame.

    Args:
        raw_dir: The directory containing raw CSVs.
        filename: The CSV filename to read.

    Returns:
        The loaded DataFrame.
    """
    path = os.path.join(raw_dir, filename)
    df = pd.read_csv(path)
    LOGGER.info("Read %d rows from %s", len(df), path)
    return df


def _coerce_numeric(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """Cast the given columns to float, coercing invalid values to NaN.

    Args:
        df: The DataFrame to modify.
        columns: Columns to coerce to numeric.

    Returns:
        The DataFrame with coerced numeric columns.
    """
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _clean_year(df: pd.DataFrame) -> pd.DataFrame:
    """Cast the ``year`` column to a nullable integer, dropping invalid rows.

    Args:
        df: The DataFrame to clean.

    Returns:
        The DataFrame with a clean integer ``year`` column.
    """
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    return df


def clean_malaria_incidence(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the raw malaria incidence DataFrame.

    Args:
        df: Raw malaria incidence data.

    Returns:
        A cleaned, deduplicated DataFrame filtered to the focus countries.
    """
    df = _clean_year(df)
    df = _coerce_numeric(df, ["incidence_per_1000", "low", "high"])
    df = df.dropna(subset=["incidence_per_1000"])
    df = df[df["country_code"].isin(AFRICAN_FOCUS)].copy()
    df["country_name"] = df["country_code"].map(COUNTRY_NAMES).fillna(df["country_name"])
    df = df.drop_duplicates(subset=["country_code", "year"])
    LOGGER.info("Cleaned malaria incidence -> %d rows", len(df))
    return df


def clean_malaria_deaths(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the raw malaria deaths DataFrame.

    Args:
        df: Raw malaria deaths data.

    Returns:
        A cleaned, deduplicated DataFrame filtered to the focus countries.
    """
    df = _clean_year(df)
    df = _coerce_numeric(df, ["deaths_count", "low", "high"])
    df = df.dropna(subset=["deaths_count"])
    df = df[df["country_code"].isin(AFRICAN_FOCUS)].copy()
    df = df.rename(columns={"low": "deaths_low", "high": "deaths_high"})
    df = df.drop_duplicates(subset=["country_code", "year"])
    LOGGER.info("Cleaned malaria deaths -> %d rows", len(df))
    return df


def clean_worldbank(df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Clean a raw World Bank indicator DataFrame.

    Args:
        df: Raw World Bank indicator data.
        value_col: The name of the numeric value column.

    Returns:
        A cleaned, deduplicated DataFrame.
    """
    df = _clean_year(df)
    df = _coerce_numeric(df, [value_col])
    df = df.dropna(subset=[value_col])
    df["country_name"] = df["country_code"].map(COUNTRY_NAMES).fillna(df["country_name"])
    df = df.drop_duplicates(subset=["country_code", "year"])
    LOGGER.info("Cleaned World Bank %s -> %d rows", value_col, len(df))
    return df


def add_burden_category(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``malaria_burden_category`` column from incidence quartiles.

    Categories: low / medium / high / very_high, based on the quartiles of the
    ``incidence_per_1000`` distribution across all focus-country/year rows.

    Args:
        df: DataFrame containing an ``incidence_per_1000`` column.

    Returns:
        The DataFrame with an added ``malaria_burden_category`` column.
    """
    try:
        df["malaria_burden_category"] = pd.qcut(
            df["incidence_per_1000"],
            q=4,
            labels=["low", "medium", "high", "very_high"],
            duplicates="drop",
        ).astype(str)
    except ValueError:
        # Fallback if too few distinct values for 4 quantiles.
        df["malaria_burden_category"] = "medium"
    LOGGER.info("Added malaria_burden_category")
    return df


def add_yoy_change(df: pd.DataFrame) -> pd.DataFrame:
    """Add a year-over-year percentage change in incidence per country.

    Args:
        df: DataFrame with ``country_code``, ``year`` and ``incidence_per_1000``.

    Returns:
        The DataFrame with an added ``incidence_yoy_change`` column (percent).
    """
    df = df.sort_values(["country_code", "year"]).copy()
    df["incidence_yoy_change"] = (
        df.groupby("country_code")["incidence_per_1000"].pct_change() * 100.0
    ).round(2)
    LOGGER.info("Added incidence_yoy_change")
    return df


def build_malaria_master(incidence: pd.DataFrame, deaths: pd.DataFrame) -> pd.DataFrame:
    """Merge incidence and deaths into a single master malaria DataFrame.

    Args:
        incidence: Cleaned malaria incidence data.
        deaths: Cleaned malaria deaths data.

    Returns:
        The enriched master malaria DataFrame.
    """
    master = incidence.merge(
        deaths[["country_code", "year", "deaths_count", "deaths_low", "deaths_high"]],
        on=["country_code", "year"],
        how="left",
    )
    master = add_burden_category(master)
    master = add_yoy_change(master)
    master["region"] = "Africa"
    ordered = [
        "country_code",
        "country_name",
        "region",
        "year",
        "incidence_per_1000",
        "low",
        "high",
        "deaths_count",
        "deaths_low",
        "deaths_high",
        "malaria_burden_category",
        "incidence_yoy_change",
    ]
    master = master[[c for c in ordered if c in master.columns]]
    LOGGER.info("Built malaria_master -> %d rows", len(master))
    return master


def build_health_indicators(
    health_exp: pd.DataFrame,
    life_exp: pd.DataFrame,
    under5: pd.DataFrame,
) -> pd.DataFrame:
    """Merge the three health indicators into one DataFrame with region labels.

    Args:
        health_exp: Cleaned health-expenditure data.
        life_exp: Cleaned life-expectancy data.
        under5: Cleaned under-5 mortality data.

    Returns:
        The merged ``health_indicators`` DataFrame.
    """
    merged = health_exp.merge(
        life_exp[["country_code", "year", "life_expectancy_years"]],
        on=["country_code", "year"],
        how="outer",
    ).merge(
        under5[["country_code", "year", "under5_mortality_per_1000"]],
        on=["country_code", "year"],
        how="outer",
    )
    merged["country_name"] = (
        merged["country_code"].map(COUNTRY_NAMES).fillna(merged["country_name"])
    )
    merged["region"] = merged["country_code"].apply(
        lambda c: "USA" if c == "USA" else "Africa"
    )
    merged = merged.dropna(subset=["country_code", "year"])
    merged["year"] = merged["year"].astype(int)
    merged = merged.drop_duplicates(subset=["country_code", "year"])
    merged = merged.sort_values(["country_code", "year"])
    ordered = [
        "country_code",
        "country_name",
        "region",
        "year",
        "health_expenditure_pct_gdp",
        "life_expectancy_years",
        "under5_mortality_per_1000",
    ]
    merged = merged[[c for c in ordered if c in merged.columns]]
    LOGGER.info("Built health_indicators -> %d rows", len(merged))
    return merged


def transform(config_path: Optional[str] = None) -> None:
    """Run the full transformation and write two processed CSVs.

    Args:
        config_path: Optional path to the configuration file.
    """
    config = load_config(config_path)
    paths = resolve_paths(config)
    raw_dir = paths["raw"]
    processed_dir = paths["processed"]

    incidence = clean_malaria_incidence(_read_csv(raw_dir, "malaria_incidence_africa.csv"))
    deaths = clean_malaria_deaths(_read_csv(raw_dir, "malaria_deaths_africa.csv"))
    health_exp = clean_worldbank(
        _read_csv(raw_dir, "health_expenditure_pct_gdp.csv"), "health_expenditure_pct_gdp"
    )
    life_exp = clean_worldbank(
        _read_csv(raw_dir, "life_expectancy.csv"), "life_expectancy_years"
    )
    under5 = clean_worldbank(
        _read_csv(raw_dir, "under5_mortality.csv"), "under5_mortality_per_1000"
    )

    malaria_master = build_malaria_master(incidence, deaths)
    health_indicators = build_health_indicators(health_exp, life_exp, under5)

    master_path = os.path.join(processed_dir, "malaria_master.csv")
    health_path = os.path.join(processed_dir, "health_indicators.csv")
    malaria_master.to_csv(master_path, index=False)
    health_indicators.to_csv(health_path, index=False)
    LOGGER.info("Wrote %s (%d rows)", master_path, len(malaria_master))
    LOGGER.info("Wrote %s (%d rows)", health_path, len(health_indicators))
    LOGGER.info("Transformation complete.")


if __name__ == "__main__":
    transform()
