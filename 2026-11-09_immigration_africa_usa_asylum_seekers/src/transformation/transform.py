"""Transformation stage for the African Asylum Seekers & Immigration Flows to the USA pipeline.

Reads the raw combined CSVs produced by ingestion, then:

1. Normalises column names and casts types (``year`` -> int, numeric metrics -> float).
2. Drops rows without a country code / year and removes duplicate ``(iso3, year)`` rows.
3. Tags each country with an African sub-region (North/West/East/Central/Southern Africa).
4. Derives analytical columns:

   * ``migrant_stock_filled``  - migrant stock carried to every year from the nearest
     reported year for the same country (World Bank publishes stock only periodically).
   * ``migration_intensity``   - ``net_migration / migrant_stock_filled``.
   * ``migration_direction``   - ``net_emigration`` / ``net_immigration`` / ``balanced``.
   * ``remittance_dependency_category`` - ``high`` (>=10% GDP), ``medium`` (>=3%),
     ``low`` (<3%) or ``unknown`` when remittances are not reported.

Outputs ``data/processed/processed_migration.csv`` and
``data/processed/processed_remittances.csv``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("transform")

NUMERIC_COLUMNS = ["net_migration", "migrant_stock_total", "migrant_stock_pct_pop", "remittances_pct_gdp"]


def load_config(path: Path | None = None) -> dict:
    """Load the YAML configuration, honouring the ``CONFIG_PATH`` env var."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve(path_value: str, env_var: str) -> Path:
    """Resolve a directory from an env var or config value relative to the project root."""
    path = Path(os.getenv(env_var) or path_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_paths(config: dict) -> Tuple[Path, Path]:
    """Return ``(raw_dir, processed_dir)``."""
    paths = config["paths"]
    return (_resolve(paths["raw_data_dir"], "RAW_DATA_DIR"),
            _resolve(paths["processed_data_dir"], "PROCESSED_DATA_DIR"))


def build_subregion_lookup(config: dict) -> Dict[str, str]:
    """Map ISO3 code -> sub-region name from config."""
    return {iso: region for region, codes in config["pipeline"]["subregions"].items() for iso in codes}


def clean_common(df: pd.DataFrame) -> pd.DataFrame:
    """Shared cleaning: column names, string trimming, type casting, null keys, duplicates."""
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("iso3", "country_name"):
        df[col] = df[col].astype("string").str.strip()
    df["iso3"] = df["iso3"].str.upper()

    before = len(df)
    df = df.dropna(subset=["iso3", "year"])
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df.dropna(subset=["year"])
    df["year"] = df["year"].astype(int)
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

    df = df.sort_values(["iso3", "year"]).drop_duplicates(subset=["iso3", "year"], keep="last")
    logger.info("Cleaning removed %d invalid/duplicate rows", before - len(df))
    return df.reset_index(drop=True)


def fill_migrant_stock(df: pd.DataFrame) -> pd.Series:
    """Carry the nearest reported migrant stock across years within each country."""
    return (df.sort_values(["iso3", "year"])
              .groupby("iso3")["migrant_stock_total"]
              .transform(lambda s: s.ffill().bfill()))


def categorise_remittances(values: pd.Series, high: float, medium: float) -> pd.Series:
    """Bucket remittances (% of GDP) into high / medium / low / unknown."""
    conditions = [values.isna(), values >= high, values >= medium]
    choices = ["unknown", "high", "medium"]
    return pd.Series(np.select(conditions, choices, default="low"), index=values.index)


def transform_migration(raw: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Clean and enrich the Africa + USA migration table."""
    df = clean_common(raw)
    df["net_migration"] = df["net_migration"].fillna(0.0)
    df["region"] = df["region"].fillna("Africa")
    df["subregion"] = df["iso3"].map(build_subregion_lookup(config)).fillna("Unclassified")

    df["migrant_stock_filled"] = fill_migrant_stock(df)
    df["migrant_stock_imputed"] = df["migrant_stock_total"].isna() & df["migrant_stock_filled"].notna()
    df["migration_intensity"] = np.where(
        df["migrant_stock_filled"] > 0, df["net_migration"] / df["migrant_stock_filled"], np.nan
    )
    df["migration_direction"] = np.select(
        [df["net_migration"] < 0, df["net_migration"] > 0], ["net_emigration", "net_immigration"],
        default="balanced",
    )
    df["net_migration_yoy_change"] = df.groupby("iso3")["net_migration"].diff()

    columns = ["country_name", "iso3", "year", "region", "subregion", "net_migration",
               "net_migration_yoy_change", "migration_direction", "migrant_stock_total",
               "migrant_stock_filled", "migrant_stock_imputed", "migrant_stock_pct_pop",
               "migration_intensity"]
    return df[columns].sort_values(["region", "country_name", "year"]).reset_index(drop=True)


def transform_remittances(raw: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Clean and enrich the African remittances table."""
    df = clean_common(raw)
    thresholds = config["pipeline"]["remittance_thresholds"]
    df["subregion"] = df["iso3"].map(build_subregion_lookup(config)).fillna("Unclassified")
    df["migrant_stock_filled"] = fill_migrant_stock(df)
    df["remittance_dependency_category"] = categorise_remittances(
        df["remittances_pct_gdp"], thresholds["high"], thresholds["medium"]
    )
    columns = ["country_name", "iso3", "year", "subregion", "remittances_pct_gdp",
               "remittance_dependency_category", "migrant_stock_total", "migrant_stock_filled",
               "migrant_stock_pct_pop"]
    return df[columns].sort_values(["country_name", "year"]).reset_index(drop=True)


def transform() -> Dict[str, Path]:
    """Run the transformation stage and return the processed file paths."""
    config = load_config()
    raw_dir, processed_dir = get_paths(config)
    paths = config["paths"]
    processed_dir.mkdir(parents=True, exist_ok=True)

    migration_raw = pd.read_csv(raw_dir / paths["migration_raw_file"])
    remittances_raw = pd.read_csv(raw_dir / paths["remittances_raw_file"])
    logger.info("Loaded raw migration (%d rows) and remittances (%d rows)",
                len(migration_raw), len(remittances_raw))

    outputs = {
        paths["migration_processed_file"]: transform_migration(migration_raw, config),
        paths["remittances_processed_file"]: transform_remittances(remittances_raw, config),
    }
    written: Dict[str, Path] = {}
    for filename, df in outputs.items():
        out = processed_dir / filename
        df.to_csv(out, index=False)
        written[filename] = out
        logger.info("Wrote %s (%d rows, %d columns)", out.name, len(df), df.shape[1])
    return written


def main() -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        transform()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.critical("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
