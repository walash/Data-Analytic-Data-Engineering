"""Loading stage: create the SQLite star schema and load processed NCD data.

Reads the CSVs written by ``transform.py`` from ``data/processed/`` and loads
them into ``data/ncd_health.db`` (path configurable) using a small star schema:

* ``dim_country``     - countries and World Bank aggregates with region tags
* ``dim_indicator``   - NCD indicator metadata
* ``fact_ncd_observation`` - one row per country x indicator x year
* ``v_africa_usa_ncd`` - convenience view joining facts to both dimensions,
  restricted to Africa, USA and benchmark aggregates

The load is idempotent: tables are dropped and recreated in one transaction.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("ncd.load")

SCHEMA_SQL = """
DROP VIEW IF EXISTS v_africa_usa_ncd;
DROP TABLE IF EXISTS fact_ncd_observation;
DROP TABLE IF EXISTS dim_indicator;
DROP TABLE IF EXISTS dim_country;

CREATE TABLE dim_country (
    iso3             TEXT PRIMARY KEY,
    country_id       TEXT,
    country_name     TEXT NOT NULL,
    region_group     TEXT NOT NULL CHECK (region_group IN
                         ('Africa', 'USA', 'Benchmark', 'Aggregate', 'Other')),
    africa_subregion TEXT,
    is_aggregate     INTEGER NOT NULL DEFAULT 0 CHECK (is_aggregate IN (0, 1))
);

CREATE TABLE dim_indicator (
    indicator_code   TEXT PRIMARY KEY,
    indicator_key    TEXT NOT NULL UNIQUE,
    indicator_name   TEXT NOT NULL,
    first_year       INTEGER,
    last_year        INTEGER,
    n_observations   INTEGER
);

CREATE TABLE fact_ncd_observation (
    observation_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    iso3               TEXT NOT NULL REFERENCES dim_country (iso3),
    indicator_code     TEXT NOT NULL REFERENCES dim_indicator (indicator_code),
    year               INTEGER NOT NULL,
    value              REAL NOT NULL CHECK (value >= 0 AND value <= 100),
    prev_year          INTEGER,
    prev_value         REAL,
    change_pp          REAL,
    change_pp_per_year REAL,
    is_latest          INTEGER NOT NULL DEFAULT 0,
    africa_rank        INTEGER,
    UNIQUE (iso3, indicator_code, year)
);

CREATE INDEX idx_fact_indicator_year ON fact_ncd_observation (indicator_code, year);
CREATE INDEX idx_fact_iso3 ON fact_ncd_observation (iso3);
CREATE INDEX idx_country_region ON dim_country (region_group, africa_subregion);

CREATE VIEW v_africa_usa_ncd AS
SELECT
    f.iso3,
    c.country_name,
    c.region_group,
    c.africa_subregion,
    i.indicator_key,
    f.indicator_code,
    i.indicator_name,
    f.year,
    f.value,
    f.change_pp,
    f.change_pp_per_year,
    f.is_latest,
    f.africa_rank
FROM fact_ncd_observation AS f
JOIN dim_country   AS c ON c.iso3 = f.iso3
JOIN dim_indicator AS i ON i.indicator_code = f.indicator_code
WHERE c.region_group IN ('Africa', 'USA', 'Benchmark');
"""

FACT_COLUMNS = [
    "iso3", "indicator_code", "year", "value", "prev_year", "prev_value",
    "change_pp", "change_pp_per_year", "is_latest", "africa_rank",
]
COUNTRY_COLUMNS = [
    "iso3", "country_id", "country_name", "region_group", "africa_subregion", "is_aggregate",
]
INDICATOR_COLUMNS = [
    "indicator_code", "indicator_key", "indicator_name", "first_year", "last_year",
    "n_observations",
]


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load YAML configuration (``$CONFIG_PATH`` or ``config/config.yaml``)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve(path_str: str) -> Path:
    """Resolve a config path relative to the project root."""
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def database_path(config: Dict[str, Any]) -> Path:
    """Return the SQLite path, honouring the ``NCD_DB_PATH`` env override."""
    return resolve(os.environ.get("NCD_DB_PATH", config["paths"]["database"]))


def read_processed(processed_dir: Path, name: str) -> pd.DataFrame:
    """Read one processed CSV, raising a clear error if it is missing."""
    path = processed_dir / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Processed file missing: {path}. Run transform.py first.")
    return pd.read_csv(path)


def _records(df: pd.DataFrame, columns: list) -> list:
    """Convert a DataFrame to a list of tuples with NaN replaced by None."""
    subset = df[columns].astype(object).where(df[columns].notna(), None)
    return [tuple(row) for row in subset.itertuples(index=False, name=None)]


def create_schema(conn: sqlite3.Connection) -> None:
    """(Re)create all tables, indexes and views."""
    conn.executescript(SCHEMA_SQL)
    logger.info("Schema created (dim_country, dim_indicator, fact_ncd_observation, view)")


def insert_table(conn: sqlite3.Connection, table: str, df: pd.DataFrame, columns: list) -> int:
    """Bulk insert ``columns`` of ``df`` into ``table``; returns rows inserted."""
    bind_params = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({bind_params})"
    rows = _records(df, columns)
    conn.executemany(sql, rows)
    logger.info("Inserted %d rows into %s", len(rows), table)
    return len(rows)


def validate(conn: sqlite3.Connection) -> Dict[str, int]:
    """Run post-load integrity checks and return table row counts."""
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("dim_country", "dim_indicator", "fact_ncd_observation", "v_africa_usa_ncd")
    }
    orphans = conn.execute(
        """SELECT COUNT(*) FROM fact_ncd_observation f
           LEFT JOIN dim_country c ON c.iso3 = f.iso3 WHERE c.iso3 IS NULL"""
    ).fetchone()[0]
    if orphans:
        raise ValueError(f"{orphans} fact rows reference unknown countries")
    logger.info("Validation passed: %s", counts)
    return counts


def load(config: Dict[str, Any]) -> Dict[str, int]:
    """Create the schema and load all processed tables in one transaction."""
    processed_dir = resolve(config["paths"]["processed_dir"])
    db_path = database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    countries = read_processed(processed_dir, "countries")
    indicators = read_processed(processed_dir, "indicators")
    facts = read_processed(processed_dir, "ncd_observations")
    for col in ("year", "prev_year", "is_latest", "africa_rank"):
        facts[col] = facts[col].astype("Int64")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            create_schema(conn)
            insert_table(conn, "dim_country", countries, COUNTRY_COLUMNS)
            insert_table(conn, "dim_indicator", indicators, INDICATOR_COLUMNS)
            insert_table(conn, "fact_ncd_observation", facts, FACT_COLUMNS)
        counts = validate(conn)
    finally:
        conn.close()
    logger.info("Database ready at %s", db_path)
    return counts


def main() -> int:
    """CLI entry point."""
    config = load_config()
    setup_logging(os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")))
    try:
        load(config)
    except (FileNotFoundError, ValueError, sqlite3.DatabaseError) as exc:
        logger.error("Load failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
