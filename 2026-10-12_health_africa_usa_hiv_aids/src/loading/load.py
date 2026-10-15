"""Loading stage for the HIV/AIDS Africa & USA pipeline.

Creates a SQLite database (default ``data/hiv_aids.db``) with a small star
schema and loads the processed country-year data into it:

* ``countries``   - dimension: one row per country (code, ISO3, name, region)
* ``indicators``  - dimension: World Bank indicator metadata
* ``hiv_metrics`` - fact: one row per country-year with all indicator values
  and derived metrics (treatment gap, 90-90-90 flag, YoY changes)

The database path can be overridden with the ``DB_PATH`` environment variable.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("hiv_aids.load")

SCHEMA_SQL = """
DROP TABLE IF EXISTS hiv_metrics;
DROP TABLE IF EXISTS indicators;
DROP TABLE IF EXISTS countries;

CREATE TABLE countries (
    country_code  TEXT PRIMARY KEY,
    iso3          TEXT NOT NULL UNIQUE,
    country_name  TEXT NOT NULL,
    region        TEXT NOT NULL CHECK (region IN ('Africa', 'USA')),
    subregion     TEXT NOT NULL
);

CREATE TABLE indicators (
    indicator_id   TEXT PRIMARY KEY,
    indicator_key  TEXT NOT NULL UNIQUE,
    value_column   TEXT NOT NULL UNIQUE,
    indicator_name TEXT NOT NULL,
    unit           TEXT NOT NULL,
    source         TEXT NOT NULL DEFAULT 'World Bank World Development Indicators'
);

CREATE TABLE hiv_metrics (
    country_code                     TEXT    NOT NULL REFERENCES countries(country_code),
    year                             INTEGER NOT NULL,
    hiv_prevalence_pct               REAL,
    art_coverage_pct                 REAL,
    hiv_incidence_per1000            REAL,
    plhiv_count                      REAL,
    health_exp_pct_gdp               REAL,
    plhiv_on_art                     REAL,
    treatment_gap                    REAL,
    treatment_gap_pct                REAL,
    meets_90_90_90_art               INTEGER,
    meets_95_95_95_art               INTEGER,
    hiv_prevalence_pct_yoy_change    REAL,
    art_coverage_pct_yoy_change      REAL,
    hiv_incidence_per1000_yoy_change REAL,
    plhiv_count_yoy_change           REAL,
    health_exp_pct_gdp_yoy_change    REAL,
    PRIMARY KEY (country_code, year)
);

CREATE INDEX idx_metrics_year ON hiv_metrics(year);
CREATE INDEX idx_countries_region ON countries(region);
"""

METRIC_COLUMNS = [
    "country_code", "year", "hiv_prevalence_pct", "art_coverage_pct",
    "hiv_incidence_per1000", "plhiv_count", "health_exp_pct_gdp", "plhiv_on_art",
    "treatment_gap", "treatment_gap_pct", "meets_90_90_90_art", "meets_95_95_95_art",
    "hiv_prevalence_pct_yoy_change", "art_coverage_pct_yoy_change",
    "hiv_incidence_per1000_yoy_change", "plhiv_count_yoy_change",
    "health_exp_pct_gdp_yoy_change",
]


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML configuration."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_db_path(config: dict[str, Any]) -> Path:
    """Return the SQLite path, honouring the ``DB_PATH`` env override."""
    db_path = Path(os.environ.get("DB_PATH") or config["paths"]["database"])
    return db_path if db_path.is_absolute() else PROJECT_ROOT / db_path


def create_schema(conn: sqlite3.Connection) -> None:
    """(Re)create all tables and indexes."""
    conn.executescript(SCHEMA_SQL)
    logger.info("Schema created: countries, indicators, hiv_metrics")


def _none_if_nan(value: Any) -> Any:
    """Convert pandas NA/NaN to None for sqlite3 parameter binding."""
    return None if pd.isna(value) else value


def load_countries(conn: sqlite3.Connection, processed: pd.DataFrame) -> int:
    """Insert one row per country."""
    dim = (processed[["country_code", "iso3", "country_name", "region", "subregion"]]
           .drop_duplicates("country_code").sort_values("country_code"))
    conn.executemany(
        "INSERT INTO countries VALUES (?, ?, ?, ?, ?)",
        dim.itertuples(index=False, name=None),
    )
    return len(dim)


def load_indicators(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    """Insert indicator metadata from config."""
    rows = [(i["id"], i["key"], i["value_column"], i["name"], i["unit"])
            for i in config["indicators"]]
    conn.executemany(
        "INSERT INTO indicators (indicator_id, indicator_key, value_column, "
        "indicator_name, unit) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def load_metrics(conn: sqlite3.Connection, processed: pd.DataFrame) -> int:
    """Insert fact rows into hiv_metrics."""
    facts = processed[METRIC_COLUMNS].copy()
    facts["year"] = facts["year"].astype(int)
    for flag in ("meets_90_90_90_art", "meets_95_95_95_art"):
        facts[flag] = facts[flag].astype("Int64")
    placeholders = ", ".join("?" for _ in METRIC_COLUMNS)
    rows = [tuple(_none_if_nan(v) for v in row)
            for row in facts.astype(object).itertuples(index=False, name=None)]
    conn.executemany(
        f"INSERT INTO hiv_metrics ({', '.join(METRIC_COLUMNS)}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def load(config: dict[str, Any] | None = None) -> dict[str, int]:
    """Create the database and load all tables; return row counts per table."""
    config = config or load_config()
    processed_path = PROJECT_ROOT / config["paths"]["processed_file"]
    if not processed_path.exists():
        raise FileNotFoundError(f"Processed file not found: {processed_path}. "
                                "Run src/transformation/transform.py first.")
    processed = pd.read_csv(processed_path, dtype={"country_code": "string"},
                            keep_default_na=False, na_values=[""])
    db_path = resolve_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        with conn:
            create_schema(conn)
            counts = {
                "countries": load_countries(conn, processed),
                "indicators": load_indicators(conn, config),
                "hiv_metrics": load_metrics(conn, processed),
            }
        violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"Foreign key violations: {violations[:5]}")
    finally:
        conn.close()
    logger.info("Loaded %s into %s", counts, db_path)
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Load processed HIV/AIDS data into SQLite.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    try:
        load(load_config(args.config))
    except (FileNotFoundError, sqlite3.Error) as exc:
        logger.error("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
