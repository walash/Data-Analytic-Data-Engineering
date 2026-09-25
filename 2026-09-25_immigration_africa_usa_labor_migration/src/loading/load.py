"""Loading module for the Africa-to-USA Labor Migration pipeline.

This module creates a SQLite database at ``data/labor_migration.db`` and loads
the processed master dataset into a normalized set of tables:

    * ``countries``            - country dimension (id + name)
    * ``net_migration``        - net migration by country/year
    * ``remittances``          - remittance flows (USD and % of GDP) by country/year
    * ``economic_indicators``  - GDP per capita, unemployment and population
    * ``master_analytics``     - the full merged fact table with derived metrics

Indexes are created on join/filter columns to keep the analytical SQL fast.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Dict

import pandas as pd
import yaml

LOGGER = logging.getLogger("loading")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.yaml")

SCHEMA: Dict[str, str] = {
    "countries": """
        CREATE TABLE countries (
            country_id   TEXT PRIMARY KEY,
            country_name TEXT NOT NULL
        )
    """,
    "net_migration": """
        CREATE TABLE net_migration (
            country_id    TEXT NOT NULL,
            year          INTEGER NOT NULL,
            net_migration REAL,
            PRIMARY KEY (country_id, year),
            FOREIGN KEY (country_id) REFERENCES countries(country_id)
        )
    """,
    "remittances": """
        CREATE TABLE remittances (
            country_id          TEXT NOT NULL,
            year                INTEGER NOT NULL,
            remittances_usd     REAL,
            remittances_pct_gdp REAL,
            PRIMARY KEY (country_id, year),
            FOREIGN KEY (country_id) REFERENCES countries(country_id)
        )
    """,
    "economic_indicators": """
        CREATE TABLE economic_indicators (
            country_id     TEXT NOT NULL,
            year           INTEGER NOT NULL,
            gdp_per_capita REAL,
            unemployment   REAL,
            population     REAL,
            PRIMARY KEY (country_id, year),
            FOREIGN KEY (country_id) REFERENCES countries(country_id)
        )
    """,
    "master_analytics": """
        CREATE TABLE master_analytics (
            country_id              TEXT NOT NULL,
            country_name            TEXT NOT NULL,
            year                    INTEGER NOT NULL,
            net_migration           REAL,
            remittances_usd         REAL,
            remittances_pct_gdp     REAL,
            gdp_per_capita          REAL,
            unemployment            REAL,
            population              REAL,
            remittance_per_capita   REAL,
            migration_rate_per_1000 REAL,
            PRIMARY KEY (country_id, year)
        )
    """,
}

INDEXES = [
    "CREATE INDEX idx_net_migration_year ON net_migration(year)",
    "CREATE INDEX idx_remittances_year ON remittances(year)",
    "CREATE INDEX idx_economic_year ON economic_indicators(year)",
    "CREATE INDEX idx_master_year ON master_analytics(year)",
    "CREATE INDEX idx_master_country ON master_analytics(country_id)",
]


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


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop any existing tables and (re)create the full schema.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for table, ddl in SCHEMA.items():
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute(ddl)
    conn.commit()
    LOGGER.info("Created %d tables", len(SCHEMA))


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create analytical indexes on frequently queried columns.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for statement in INDEXES:
        cursor.execute(statement)
    conn.commit()
    LOGGER.info("Created %d indexes", len(INDEXES))


def load_master(config_path: str = DEFAULT_CONFIG_PATH) -> str:
    """Create the database and load all tables from the processed master CSV.

    Args:
        config_path: Path to ``config.yaml``.

    Returns:
        Absolute path to the created SQLite database.

    Raises:
        FileNotFoundError: If the processed master dataset does not exist.
    """
    config = load_config(config_path)
    configure_logging(config["pipeline"].get("log_level", "INFO"))

    processed_dir = os.path.join(PROJECT_ROOT, config["paths"]["processed_data_dir"])
    master_path = os.path.join(processed_dir, config["paths"]["processed_master_file"])
    if not os.path.exists(master_path):
        raise FileNotFoundError(
            f"Processed master file not found: {master_path}. Run transform.py first."
        )

    db_path = os.path.join(PROJECT_ROOT, config["paths"]["database_path"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    master = pd.read_csv(master_path)

    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)

        # Country dimension
        countries = (
            master[["country_id", "country_name"]]
            .drop_duplicates()
            .sort_values("country_id")
        )
        countries.to_sql("countries", conn, if_exists="append", index=False)

        # Net migration fact
        master[["country_id", "year", "net_migration"]].to_sql(
            "net_migration", conn, if_exists="append", index=False
        )

        # Remittances fact
        master[
            ["country_id", "year", "remittances_usd", "remittances_pct_gdp"]
        ].to_sql("remittances", conn, if_exists="append", index=False)

        # Economic indicators fact
        master[
            ["country_id", "year", "gdp_per_capita", "unemployment", "population"]
        ].to_sql("economic_indicators", conn, if_exists="append", index=False)

        # Full master analytics table
        master.to_sql("master_analytics", conn, if_exists="append", index=False)

        create_indexes(conn)
        LOGGER.info("Loaded %d master rows into %s", len(master), db_path)
    finally:
        conn.close()

    return db_path


def main() -> None:
    """Command-line entry point for the loading stage."""
    load_master()


if __name__ == "__main__":
    main()
