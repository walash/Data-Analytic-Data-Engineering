"""Transformation stage for the Deepwater Offshore Oil Production project.

Reads ``data/raw/oil_production_merged.csv`` (OWID + World Bank merge) and
produces two analysis-ready tables in ``data/processed/``:

* ``oil_production_processed.csv`` - one row per country-year with cleaned
  production figures, region enrichment (Africa vs USA), unit conversions
  and year-over-year / cumulative metrics.
* ``oil_rents_processed.csv`` - one row per country-year with oil rents
  (% of GDP), GDP and the implied oil-rent value in USD.

Cleaning rules
--------------
* Column names are normalised; numeric columns are coerced with
  ``pd.to_numeric(errors="coerce")``; ``year`` is cast to int.
* Exact duplicates and duplicate (iso_code, year) keys are dropped
  (last occurrence kept).
* Rows with no production value are kept in the rents table but flagged
  ``has_production = 0`` in the production table, so SQL aggregates can
  filter them explicitly instead of silently treating gaps as zero.
* ``oil_consumption_twh`` / ``fossil_fuel_consumption`` are sparse in OWID
  for African countries and are retained as nullable columns.

Derived metrics
---------------
* ``oil_production_kbd = oil_production_twh * 1000 / 8.76 / 24`` - the
  project's specified TWh/year -> kbd conversion.
* ``oil_production_kbd_bbl`` - physical crude-equivalent barrels per day
  using 1.7 MWh per barrel (cross-check figure).
* ``yoy_change_pct``, ``cumulative_twh``, ``share_of_group_pct``,
  ``prod_mwh_per_capita`` and ``oil_rents_usd``.

Usage::

    python src/transformation/transform.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

NUMERIC_COLUMNS = [
    "oil_production_twh", "oil_consumption_twh", "oil_prod_per_capita",
    "fossil_fuel_consumption", "gdp", "population", "oil_rents_pct_gdp",
]
REQUIRED_COLUMNS = ["country", "iso_code", "year"] + NUMERIC_COLUMNS

FALLBACK_REGIONS = {
    "NGA": "Africa", "AGO": "Africa", "GNQ": "Africa", "COG": "Africa",
    "GAB": "Africa", "CMR": "Africa", "GHA": "Africa", "USA": "USA",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | transform | %(message)s",
)
logger = logging.getLogger("transform")


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load YAML config; returns sensible defaults if the file is absent."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    logger.warning("Config %s not found, using built-in defaults", cfg_path)
    return {
        "paths": {
            "raw": "data/raw", "processed": "data/processed",
            "raw_files": {"merged": "oil_production_merged.csv"},
            "processed_files": {"production": "oil_production_processed.csv",
                                "rents": "oil_rents_processed.csv"},
        },
        "pipeline": {"kbd_divisor_hours": 8.76, "kbd_divisor_day": 24,
                     "mwh_per_barrel": 1.7},
        "countries": {k: {"region": v} for k, v in FALLBACK_REGIONS.items()},
    }


def _resolve(env_var: str, configured: str) -> Path:
    """Resolve a directory from an env override or config value."""
    path = Path(os.getenv(env_var, configured))
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_raw(path: Path) -> pd.DataFrame:
    """Read the merged raw CSV and validate that required columns exist."""
    if not path.exists():
        raise FileNotFoundError(f"Raw input not found: {path}. Run ingestion first.")
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Raw input missing columns: {missing}")
    logger.info("Read %d raw rows from %s", len(df), path)
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Trim strings, cast types, drop invalid keys and deduplicate."""
    out = df.copy()
    out["country"] = out["country"].astype("string").str.strip()
    out["iso_code"] = out["iso_code"].astype("string").str.strip().str.upper()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    before = len(out)
    out = out.dropna(subset=["iso_code", "year"])
    out["year"] = out["year"].astype(int)
    out = out.drop_duplicates()
    out = out.drop_duplicates(subset=["iso_code", "year"], keep="last")
    # Negative physical quantities are data errors -> treat as missing.
    for col in ["oil_production_twh", "gdp", "population", "oil_rents_pct_gdp"]:
        out.loc[out[col] < 0, col] = pd.NA
    out = out.sort_values(["iso_code", "year"]).reset_index(drop=True)
    logger.info("Cleaned: %d -> %d rows; nulls per column: %s", before, len(out),
                out[NUMERIC_COLUMNS].isna().sum().to_dict())
    return out


def enrich(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Attach region and basin metadata from config."""
    countries = config.get("countries", {})
    out = df.copy()
    out["region"] = out["iso_code"].map(
        lambda iso: countries.get(iso, {}).get("region", FALLBACK_REGIONS.get(iso, "Other"))
    )
    out["deepwater_basin"] = out["iso_code"].map(
        lambda iso: countries.get(iso, {}).get("deepwater_basin", "Unknown")
    )
    return out


def build_production(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Compute production-derived metrics and return the production table."""
    p = config.get("pipeline", {})
    hours = p.get("kbd_divisor_hours", 8.76)
    day = p.get("kbd_divisor_day", 24)
    mwh_bbl = p.get("mwh_per_barrel", 1.7)

    out = df.copy()
    twh = out["oil_production_twh"]
    out["has_production"] = twh.notna().astype(int)
    out["oil_production_kbd"] = (twh * 1000 / hours / day).round(2)
    # TWh -> MWh (1e6) / MWh per bbl -> bbl/yr / 365 -> bbl/d / 1000 -> kbd
    out["oil_production_kbd_bbl"] = (twh * 1e6 / mwh_bbl / 365 / 1000).round(2)
    out["prod_mwh_per_capita"] = (twh * 1e6 / out["population"]).round(3)

    grp = out.groupby("iso_code", group_keys=False)
    out["yoy_change_twh"] = grp["oil_production_twh"].diff().round(3)
    prev = grp["oil_production_twh"].shift(1)
    out["yoy_change_pct"] = ((twh - prev) / prev * 100).round(2)
    out["cumulative_twh"] = grp["oil_production_twh"].apply(
        lambda s: s.fillna(0).cumsum()).round(3)

    region_year_total = out.groupby(["region", "year"])["oil_production_twh"].transform("sum")
    out["share_of_region_pct"] = (twh / region_year_total * 100).round(2)

    cols = [
        "country", "iso_code", "region", "deepwater_basin", "year",
        "oil_production_twh", "oil_production_kbd", "oil_production_kbd_bbl",
        "oil_prod_per_capita", "prod_mwh_per_capita", "oil_consumption_twh",
        "fossil_fuel_consumption", "population", "gdp", "yoy_change_twh",
        "yoy_change_pct", "cumulative_twh", "share_of_region_pct", "has_production",
    ]
    return out[cols]


def build_rents(df: pd.DataFrame) -> pd.DataFrame:
    """Return the oil-rents table with implied USD rent value and GDP per capita."""
    out = df[["country", "iso_code", "region", "year", "oil_rents_pct_gdp",
              "gdp", "population"]].copy()
    out["oil_rents_usd"] = (out["gdp"] * out["oil_rents_pct_gdp"] / 100).round(0)
    out["gdp_per_capita"] = (out["gdp"] / out["population"]).round(2)
    out["oil_rents_pct_gdp"] = out["oil_rents_pct_gdp"].round(4)
    out["has_rents"] = out["oil_rents_pct_gdp"].notna().astype(int)
    return out


def validate(production: pd.DataFrame, rents: pd.DataFrame) -> None:
    """Basic data-quality assertions; raise ValueError on violation."""
    for name, frame in (("production", production), ("rents", rents)):
        if frame.empty:
            raise ValueError(f"{name} table is empty")
        if frame.duplicated(["iso_code", "year"]).any():
            raise ValueError(f"{name} table has duplicate iso_code/year keys")
    if (production["oil_production_twh"].dropna() < 0).any():
        raise ValueError("negative production values after cleaning")
    if not set(production["region"]).issubset({"Africa", "USA", "Other"}):
        raise ValueError("unexpected region labels")
    logger.info("Validation passed")


def transform(config_path: str | None = None) -> dict[str, Any]:
    """Run the transformation stage end-to-end and return a summary."""
    config = load_config(config_path)
    paths = config["paths"]
    raw = _resolve("DATA_RAW_DIR", paths["raw"])
    processed = _resolve("DATA_PROCESSED_DIR", paths["processed"])
    processed.mkdir(parents=True, exist_ok=True)

    df = enrich(clean(read_raw(raw / paths["raw_files"]["merged"])), config)
    production = build_production(df, config)
    rents = build_rents(df)
    validate(production, rents)

    prod_path = processed / paths["processed_files"]["production"]
    rents_path = processed / paths["processed_files"]["rents"]
    production.to_csv(prod_path, index=False)
    rents.to_csv(rents_path, index=False)
    logger.info("Wrote %d rows -> %s", len(production), prod_path)
    logger.info("Wrote %d rows -> %s", len(rents), rents_path)
    return {
        "production_rows": len(production),
        "rents_rows": len(rents),
        "production_path": str(prod_path),
        "rents_path": str(rents_path),
    }


def main() -> int:
    """CLI entry point."""
    try:
        transform()
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit 1
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
