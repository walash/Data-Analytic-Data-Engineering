"""Data loading module for the Africa-to-USA immigration pipeline.

This module reads the processed CSV files produced by
:mod:`src.transformation.transform` from ``data/processed/`` and loads them into
a SQLite database located at ``data/immigration_analytics.db``.

Tables created:

* ``african_migration``  - per country/year net migration + derived rate
* ``remittances``        - per country/year remittances (% of GDP)
* ``population``         - per country/year total population
* ``asylum_decisions``   - UNHCR USA asylum decisions by year/procedure type
* ``country_summary``    - per-country aggregate metrics

Indexes are created on the most common join/filter columns to keep the
analytical queries in ``sql/queries.sql`` fast.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("load")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# DDL statements for each table
SCHEMA: Dict[str, str] = {
    "african_migration": """
        CREATE TABLE african_migration (
            country                  TEXT,
            iso3                     TEXT,
            year                     INTEGER,
            region                   TEXT,
            net_migration            REAL,
            population               REAL,
            migration_rate_per_1000  REAL,
            PRIMARY KEY (iso3, year)
        );
    """,
    "remittances": """
        CREATE TABLE remittances (
            country              TEXT,
            iso3                 TEXT,
            year                 INTEGER,
            remittances_pct_gdp  REAL,
            PRIMARY KEY (iso3, year)
        );
    """,
    "population": """
        CREATE TABLE population (
            country     TEXT,
            iso3        TEXT,
            year        INTEGER,
            population  REAL,
            PRIMARY KEY (iso3, year)
        );
    """,
    "asylum_decisions": """
        CREATE TABLE asylum_decisions (
            year             INTEGER,
            procedure_type   TEXT,
            procedure_label  TEXT,
            recognized       INTEGER,
            other            INTEGER,
            rejected         INTEGER,
            closed           INTEGER,
            total            INTEGER
        );
    """,
    "country_summary": """
        CREATE TABLE country_summary (
            country                       TEXT,
            iso3                          TEXT PRIMARY KEY,
            region                        TEXT,
            latest_year                   INTEGER,
            latest_net_migration          REAL,
            latest_population             REAL,
            avg_net_migration             REAL,
            avg_remittances_pct_gdp       REAL,
            avg_migration_rate_per_1000   REAL,
            total_net_migration           REAL
        );
    """,
}

# Indexes to accelerate analytical queries
INDEXES = [
    "CREATE INDEX idx_migration_year ON african_migration(year);",
    "CREATE INDEX idx_migration_region ON african_migration(region);",
    "CREATE INDEX idx_remittances_year ON remittances(year);",
    "CREATE INDEX idx_population_year ON population(year);",
    "CREATE INDEX idx_asylum_year ON asylum_decisions(year);",
]


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


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve the processed data directory and database file path.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Dictionary with ``processed`` and ``database`` Path objects.
    """
    processed = PROJECT_ROOT / config["paths"]["processed_dir"]
    database = PROJECT_ROOT / config["paths"]["database"]
    database.parent.mkdir(parents=True, exist_ok=True)
    return {"processed": processed, "database": database}


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and (re)create all tables defined in :data:`SCHEMA`.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for table, ddl in SCHEMA.items():
        cursor.execute(f"DROP TABLE IF EXISTS {table};")
        cursor.execute(ddl)
        LOGGER.info("Created table %s", table)
    conn.commit()


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a processed CSV file, returning an empty DataFrame if absent.

    Args:
        path: Path to the CSV file.

    Returns:
        The loaded DataFrame (empty if the file does not exist).
    """
    if not path.exists():
        LOGGER.warning("Processed file missing: %s", path.name)
        return pd.DataFrame()
    df = pd.read_csv(path)
    LOGGER.info("Read %s: %d rows", path.name, len(df))
    return df


def load_migration(conn: sqlite3.Connection, processed: Path) -> None:
    """Load net-migration data into the ``african_migration`` table.

    Args:
        conn: Open SQLite connection.
        processed: Path to the processed data directory.
    """
    df = _read_csv(processed / "unified_migration.csv")
    if df.empty:
        return
    cols = ["country", "iso3", "year", "region", "net_migration",
            "population", "migration_rate_per_1000"]
    df = df[[c for c in cols if c in df.columns]].dropna(subset=["iso3", "year"])
    df = df.drop_duplicates(subset=["iso3", "year"], keep="first")
    df.to_sql("african_migration", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d rows into african_migration", len(df))


def load_remittances(conn: sqlite3.Connection, processed: Path) -> None:
    """Load remittance data into the ``remittances`` table.

    Args:
        conn: Open SQLite connection.
        processed: Path to the processed data directory.
    """
    df = _read_csv(processed / "remittances_clean.csv")
    if df.empty:
        return
    df = df[["country", "iso3", "year", "remittances_pct_gdp"]]
    df = df.drop_duplicates(subset=["iso3", "year"], keep="first")
    df.to_sql("remittances", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d rows into remittances", len(df))


def load_population(conn: sqlite3.Connection, processed: Path) -> None:
    """Load population data into the ``population`` table.

    Args:
        conn: Open SQLite connection.
        processed: Path to the processed data directory.
    """
    df = _read_csv(processed / "population_clean.csv")
    if df.empty:
        return
    df = df[["country", "iso3", "year", "population"]]
    df = df.drop_duplicates(subset=["iso3", "year"], keep="first")
    df.to_sql("population", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d rows into population", len(df))


def load_asylum_decisions(conn: sqlite3.Connection, processed: Path) -> None:
    """Load USA asylum-decision data into the ``asylum_decisions`` table.

    Args:
        conn: Open SQLite connection.
        processed: Path to the processed data directory.
    """
    df = _read_csv(processed / "asylum_decisions_usa.csv")
    if df.empty:
        return
    cols = ["year", "procedure_type", "procedure_label",
            "recognized", "other", "rejected", "closed", "total"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_sql("asylum_decisions", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d rows into asylum_decisions", len(df))


def load_country_summary(conn: sqlite3.Connection, processed: Path) -> None:
    """Load the per-country summary into the ``country_summary`` table.

    Args:
        conn: Open SQLite connection.
        processed: Path to the processed data directory.
    """
    df = _read_csv(processed / "country_summary.csv")
    if df.empty:
        return
    df = df.drop_duplicates(subset=["iso3"], keep="first")
    df.to_sql("country_summary", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d rows into country_summary", len(df))


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create analytical indexes.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for stmt in INDEXES:
        cursor.execute(stmt)
    conn.commit()
    LOGGER.info("Created %d indexes", len(INDEXES))


def load(config_path: Optional[Path] = None) -> None:
    """Run the full loading pipeline.

    Args:
        config_path: Optional path to the configuration file.
    """
    config = load_config(config_path)
    paths = get_paths(config)
    processed, database = paths["processed"], paths["database"]

    LOGGER.info("Loading processed data into %s", database)
    conn = sqlite3.connect(str(database))
    try:
        create_schema(conn)
        load_migration(conn, processed)
        load_remittances(conn, processed)
        load_population(conn, processed)
        load_asylum_decisions(conn, processed)
        load_country_summary(conn, processed)
        create_indexes(conn)
        conn.commit()
    finally:
        conn.close()
    LOGGER.info("Loading complete.")


def main() -> None:
    """Command-line entry point."""
    load()


if __name__ == "__main__":
    main()
