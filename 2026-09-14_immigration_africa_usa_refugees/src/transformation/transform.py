"""Data transformation module for the Africa-to-USA immigration pipeline.

This module reads the raw files produced by :mod:`src.ingestion.ingest` from
``data/raw/`` and turns them into clean, enriched, analysis-ready CSV files in
``data/processed/``.

Responsibilities:

* Clean and type-cast the three World Bank CSV extracts (migration,
  remittances, population).
* Parse the UNHCR global refugee JSON and USA asylum-decisions JSON.
* Handle nulls, deduplicate, and normalise column types.
* Merge migration, remittances and population into a single unified table.
* Derive analytical metrics:

    - ``migration_rate_per_1000`` = net_migration / population * 1000
    - ``remittance_per_capita``   = remittances_pct_gdp derived per capita proxy
    - regional segmentation (North Africa vs Sub-Saharan Africa)

* Produce a ``country_summary`` table with per-country aggregates.

Outputs written to ``data/processed/``:

    - ``migration_clean.csv``
    - ``remittances_clean.csv``
    - ``population_clean.csv``
    - ``unified_migration.csv``
    - ``refugees_global.csv``
    - ``asylum_decisions_usa.csv``
    - ``country_summary.csv``
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("transform")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


# --------------------------------------------------------------------------- #
# Configuration & path helpers
# --------------------------------------------------------------------------- #
def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration file.

    Args:
        config_path: Optional explicit path to ``config.yaml``.

    Returns:
        Parsed configuration dictionary.
    """
    path = config_path or Path(os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve raw/processed directories and ensure the processed dir exists.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Dictionary with ``raw`` and ``processed`` Path objects.
    """
    raw = PROJECT_ROOT / config["paths"]["raw_dir"]
    processed = PROJECT_ROOT / config["paths"]["processed_dir"]
    processed.mkdir(parents=True, exist_ok=True)
    return {"raw": raw, "processed": processed}


# --------------------------------------------------------------------------- #
# World Bank CSV cleaning
# --------------------------------------------------------------------------- #
def clean_worldbank_csv(path: Path, value_col: str) -> pd.DataFrame:
    """Load and clean a single World Bank CSV extract.

    Cleaning steps: drop rows with missing keys or values, cast ``year`` to int
    and the value column to float, strip whitespace from strings, and drop
    duplicate ``(iso3, year)`` pairs keeping the first occurrence.

    Args:
        path: Path to the raw CSV file.
        value_col: Name of the value column (e.g. ``net_migration``).

    Returns:
        A cleaned :class:`pandas.DataFrame`.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(subset=["iso3", "year", value_col])
    df["country"] = df["country"].astype(str).str.strip()
    df["iso3"] = df["iso3"].astype(str).str.strip().str.upper()
    df["year"] = df["year"].astype(int)
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[value_col])
    df = df.drop_duplicates(subset=["iso3", "year"], keep="first")
    df = df.sort_values(["country", "year"]).reset_index(drop=True)
    LOGGER.info("Cleaned %s: %d rows", path.name, len(df))
    return df


# --------------------------------------------------------------------------- #
# UNHCR JSON parsing
# --------------------------------------------------------------------------- #
def _to_int(value: Any) -> int:
    """Coerce a UNHCR value (which may be the string ``"-"`` or ``"0"``) to int.

    Args:
        value: A raw value from a UNHCR record.

    Returns:
        The integer value, or ``0`` when the value is missing/non-numeric.
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if text in ("", "-"):
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_refugees_global(path: Path) -> pd.DataFrame:
    """Parse the UNHCR global refugee population JSON into a yearly DataFrame.

    Args:
        path: Path to ``unhcr_global_refugees.json``.

    Returns:
        A DataFrame with one row per year and columns for refugees, asylum
        seekers, IDPs, returned refugees, stateless persons and others.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    rows: List[Dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "year": _to_int(item.get("year")),
                "refugees": _to_int(item.get("refugees")),
                "asylum_seekers": _to_int(item.get("asylum_seekers")),
                "returned_refugees": _to_int(item.get("returned_refugees")),
                "idps": _to_int(item.get("idps")),
                "returned_idps": _to_int(item.get("returned_idps")),
                "stateless": _to_int(item.get("stateless")),
                "ooc": _to_int(item.get("ooc")),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        # Aggregate in case of multiple records per year
        df = df.groupby("year", as_index=False).sum(numeric_only=True)
        df = df.sort_values("year").reset_index(drop=True)
    LOGGER.info("Parsed global refugees: %d yearly rows", len(df))
    return df


# Human-readable labels for UNHCR asylum procedure types
PROCEDURE_TYPE_LABELS = {
    "G": "Government",
    "U": "UNHCR",
    "J": "Joint",
    "-": "Unknown",
}


def parse_asylum_decisions(path: Path) -> pd.DataFrame:
    """Parse the UNHCR USA asylum-decisions JSON into a tidy DataFrame.

    Args:
        path: Path to ``unhcr_asylum_decisions_usa.json``.

    Returns:
        A DataFrame with columns: ``year``, ``procedure_type``,
        ``procedure_label``, ``recognized``, ``other``, ``rejected``,
        ``closed`` and ``total``.
    """
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    rows: List[Dict[str, Any]] = []
    for item in items:
        ptype = str(item.get("procedure_type", "-")).strip() or "-"
        rows.append(
            {
                "year": _to_int(item.get("year")),
                "procedure_type": ptype,
                "procedure_label": PROCEDURE_TYPE_LABELS.get(ptype, ptype),
                "recognized": _to_int(item.get("dec_recognized")),
                "other": _to_int(item.get("dec_other")),
                "rejected": _to_int(item.get("dec_rejected")),
                "closed": _to_int(item.get("dec_closed")),
                "total": _to_int(item.get("dec_total")),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = (
            df.groupby(["year", "procedure_type", "procedure_label"], as_index=False)
            .sum(numeric_only=True)
            .sort_values(["year", "procedure_type"])
            .reset_index(drop=True)
        )
    LOGGER.info("Parsed asylum decisions: %d rows", len(df))
    return df


# --------------------------------------------------------------------------- #
# Enrichment / merging
# --------------------------------------------------------------------------- #
def tag_region(iso3: str, north_africa: List[str]) -> str:
    """Classify a country into a broad African region.

    Args:
        iso3: ISO3 country code.
        north_africa: List of North African ISO3 codes from config.

    Returns:
        ``"North Africa"`` or ``"Sub-Saharan Africa"``.
    """
    return "North Africa" if iso3 in north_africa else "Sub-Saharan Africa"


def build_unified(
    migration: pd.DataFrame,
    remittances: pd.DataFrame,
    population: pd.DataFrame,
    north_africa: List[str],
) -> pd.DataFrame:
    """Merge the three World Bank tables and derive analytical metrics.

    Args:
        migration: Cleaned net-migration DataFrame.
        remittances: Cleaned remittances DataFrame.
        population: Cleaned population DataFrame.
        north_africa: List of North African ISO3 codes for regional tagging.

    Returns:
        A unified DataFrame keyed on ``(iso3, year)`` with derived metrics.
    """
    unified = migration.merge(
        remittances[["iso3", "year", "remittances_pct_gdp"]],
        on=["iso3", "year"], how="outer",
    ).merge(
        population[["iso3", "year", "population"]],
        on=["iso3", "year"], how="outer",
    )

    # Backfill the country name from any of the three sources
    name_lookup = (
        pd.concat([migration[["iso3", "country"]],
                   remittances[["iso3", "country"]],
                   population[["iso3", "country"]]])
        .dropna()
        .drop_duplicates(subset=["iso3"])
        .set_index("iso3")["country"]
        .to_dict()
    )
    unified["country"] = unified["iso3"].map(name_lookup)

    # Derived metric: net migration per 1,000 population
    unified["migration_rate_per_1000"] = np.where(
        (unified["population"].notna()) & (unified["population"] > 0)
        & (unified["net_migration"].notna()),
        unified["net_migration"] / unified["population"] * 1000.0,
        np.nan,
    )

    # Derived metric: remittance value per capita (USD proxy).
    # remittances_pct_gdp is % of GDP; without absolute GDP we express a
    # normalised per-capita intensity = pct_gdp value retained per person.
    unified["remittance_per_capita"] = np.where(
        (unified["population"].notna()) & (unified["population"] > 0)
        & (unified["remittances_pct_gdp"].notna()),
        unified["remittances_pct_gdp"] / unified["population"] * 1_000_000.0,
        np.nan,
    )

    unified["region"] = unified["iso3"].apply(lambda c: tag_region(c, north_africa))

    unified = unified[
        [
            "country", "iso3", "year", "region",
            "net_migration", "remittances_pct_gdp", "population",
            "migration_rate_per_1000", "remittance_per_capita",
        ]
    ].sort_values(["country", "year"]).reset_index(drop=True)

    # Round derived metrics for readability
    unified["migration_rate_per_1000"] = unified["migration_rate_per_1000"].round(3)
    unified["remittance_per_capita"] = unified["remittance_per_capita"].round(6)
    LOGGER.info("Built unified dataset: %d rows", len(unified))
    return unified


def build_country_summary(unified: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the unified dataset into a per-country summary.

    Args:
        unified: The merged/enriched dataset.

    Returns:
        A summary DataFrame with one row per country containing latest-year
        values and multi-year averages.
    """
    records: List[Dict[str, Any]] = []
    for iso3, group in unified.groupby("iso3"):
        group_valid = group.dropna(subset=["year"])
        latest = group_valid.sort_values("year").iloc[-1]
        records.append(
            {
                "country": latest["country"],
                "iso3": iso3,
                "region": latest["region"],
                "latest_year": int(latest["year"]),
                "latest_net_migration": latest.get("net_migration"),
                "latest_population": latest.get("population"),
                "avg_net_migration": round(group["net_migration"].mean(skipna=True), 1)
                if group["net_migration"].notna().any() else None,
                "avg_remittances_pct_gdp": round(
                    group["remittances_pct_gdp"].mean(skipna=True), 4)
                if group["remittances_pct_gdp"].notna().any() else None,
                "avg_migration_rate_per_1000": round(
                    group["migration_rate_per_1000"].mean(skipna=True), 3)
                if group["migration_rate_per_1000"].notna().any() else None,
                "total_net_migration": round(group["net_migration"].sum(skipna=True), 0)
                if group["net_migration"].notna().any() else None,
            }
        )
    summary = pd.DataFrame(records).sort_values("country").reset_index(drop=True)
    LOGGER.info("Built country summary: %d countries", len(summary))
    return summary


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def transform(config_path: Optional[Path] = None) -> None:
    """Run the full transformation pipeline.

    Args:
        config_path: Optional path to the configuration file.
    """
    config = load_config(config_path)
    paths = resolve_paths(config)
    raw, processed = paths["raw"], paths["processed"]
    north_africa = config.get("north_africa", [])

    # 1. Clean World Bank extracts
    migration = clean_worldbank_csv(raw / "africa_migration_worldbank.csv", "net_migration")
    remittances = clean_worldbank_csv(raw / "africa_remittances_worldbank.csv", "remittances_pct_gdp")
    population = clean_worldbank_csv(raw / "africa_population_worldbank.csv", "population")

    migration.to_csv(processed / "migration_clean.csv", index=False)
    remittances.to_csv(processed / "remittances_clean.csv", index=False)
    population.to_csv(processed / "population_clean.csv", index=False)

    # 2. Merge + enrich
    unified = build_unified(migration, remittances, population, north_africa)
    unified.to_csv(processed / "unified_migration.csv", index=False)

    # 3. UNHCR data
    refugees = parse_refugees_global(raw / "unhcr_global_refugees.json")
    refugees.to_csv(processed / "refugees_global.csv", index=False)

    asylum = parse_asylum_decisions(raw / "unhcr_asylum_decisions_usa.json")
    asylum.to_csv(processed / "asylum_decisions_usa.csv", index=False)

    # 4. Country summary
    summary = build_country_summary(unified)
    summary.to_csv(processed / "country_summary.csv", index=False)

    LOGGER.info("Transformation complete. Outputs in %s", processed)


def main() -> None:
    """Command-line entry point."""
    transform()


if __name__ == "__main__":
    main()
