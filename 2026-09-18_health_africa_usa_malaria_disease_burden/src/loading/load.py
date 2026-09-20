"""Loading module for the Malaria & Infectious Disease Burden pipeline.

Reads the processed CSVs from ``data/processed/`` and loads them into a SQLite
database at ``data/health_disease_burden.db``. Four tables are created (with
``CREATE TABLE IF NOT EXISTS``) plus supporting indexes on ``country_code`` and
``year``:

* ``malaria_incidence``  — per country/year incidence + burden category.
* ``malaria_deaths``     — per country/year estimated deaths.
* ``health_indicators``  — health expenditure, life expectancy, under-5 mortality.
* ``country_metadata``   — country_code, country_name, region, sub_region.

Run as a module::

    python -m src.loading.load
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Dict, Optional

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("load")

# Static country metadata for the focus countries + USA comparator.
COUNTRY_METADATA = [
    ("KEN", "Kenya", "Africa", "Eastern Africa"),
    ("ETH", "Ethiopia", "Africa", "Eastern Africa"),
    ("NGA", "Nigeria", "Africa", "Western Africa"),
    ("GHA", "Ghana", "Africa", "Western Africa"),
    ("ZAF", "South Africa", "Africa", "Southern Africa"),
    ("UGA", "Uganda", "Africa", "Eastern Africa"),
    ("TZA", "Tanzania", "Africa", "Eastern Africa"),
    ("MOZ", "Mozambique", "Africa", "Eastern Africa"),
    ("ZMB", "Zambia", "Africa", "Eastern Africa"),
    ("MWI", "Malawi", "Africa", "Eastern Africa"),
    ("USA", "United States", "USA", "Northern America"),
]

SCHEMA: Dict[str, str] = {
    "malaria_incidence": """
        CREATE TABLE IF NOT EXISTS malaria_incidence (
            country_code            TEXT NOT NULL,
            country_name            TEXT,
            region                  TEXT,
            year                    INTEGER NOT NULL,
            incidence_per_1000      REAL,
            low                     REAL,
            high                    REAL,
            malaria_burden_category TEXT,
            incidence_yoy_change    REAL,
            PRIMARY KEY (country_code, year)
        )
    """,
    "malaria_deaths": """
        CREATE TABLE IF NOT EXISTS malaria_deaths (
            country_code TEXT NOT NULL,
            country_name TEXT,
            region       TEXT,
            year         INTEGER NOT NULL,
            deaths_count REAL,
            deaths_low   REAL,
            deaths_high  REAL,
            PRIMARY KEY (country_code, year)
        )
    """,
    "health_indicators": """
        CREATE TABLE IF NOT EXISTS health_indicators (
            country_code               TEXT NOT NULL,
            country_name               TEXT,
            region                     TEXT,
            year                       INTEGER NOT NULL,
            health_expenditure_pct_gdp REAL,
            life_expectancy_years      REAL,
            under5_mortality_per_1000  REAL,
            PRIMARY KEY (country_code, year)
        )
    """,
    "country_metadata": """
        CREATE TABLE IF NOT EXISTS country_metadata (
            country_code TEXT PRIMARY KEY,
            country_name TEXT NOT NULL,
            region       TEXT NOT NULL,
            sub_region   TEXT
        )
    """,
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_incidence_country ON malaria_incidence(country_code)",
    "CREATE INDEX IF NOT EXISTS idx_incidence_year ON malaria_incidence(year)",
    "CREATE INDEX IF NOT EXISTS idx_deaths_country ON malaria_deaths(country_code)",
    "CREATE INDEX IF NOT EXISTS idx_deaths_year ON malaria_deaths(year)",
    "CREATE INDEX IF NOT EXISTS idx_health_country ON health_indicators(country_code)",
    "CREATE INDEX IF NOT EXISTS idx_health_year ON health_indicators(year)",
]


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


def get_paths(config: dict) -> Dict[str, str]:
    """Resolve absolute processed-data and database paths.

    Args:
        config: The parsed configuration dictionary.

    Returns:
        A dictionary with ``processed`` and ``db`` paths.
    """
    root = _project_root()
    processed = os.path.join(root, config["paths"]["processed_data_dir"])
    db_path = os.path.join(root, config["paths"]["db_path"])
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return {"processed": processed, "db": db_path}


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist.

    Args:
        conn: An open SQLite connection.
    """
    cur = conn.cursor()
    for name, ddl in SCHEMA.items():
        cur.execute(ddl)
        LOGGER.info("Ensured table %s", name)
    for idx in INDEXES:
        cur.execute(idx)
    conn.commit()
    LOGGER.info("Schema and indexes ready")


def _read_processed(processed_dir: str, filename: str) -> pd.DataFrame:
    """Read a processed CSV into a DataFrame.

    Args:
        processed_dir: Directory containing processed CSVs.
        filename: The CSV filename.

    Returns:
        The loaded DataFrame.
    """
    path = os.path.join(processed_dir, filename)
    df = pd.read_csv(path)
    LOGGER.info("Read %d rows from %s", len(df), path)
    return df


def load_country_metadata(conn: sqlite3.Connection) -> None:
    """Populate the ``country_metadata`` table (idempotent upsert).

    Args:
        conn: An open SQLite connection.
    """
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO country_metadata "
        "(country_code, country_name, region, sub_region) VALUES (?, ?, ?, ?)",
        COUNTRY_METADATA,
    )
    conn.commit()
    LOGGER.info("Loaded %d country_metadata rows", len(COUNTRY_METADATA))


def load_malaria(conn: sqlite3.Connection, processed_dir: str) -> None:
    """Load malaria master data into the incidence and deaths tables.

    Args:
        conn: An open SQLite connection.
        processed_dir: Directory containing processed CSVs.
    """
    master = _read_processed(processed_dir, "malaria_master.csv")

    incidence_cols = [
        "country_code",
        "country_name",
        "region",
        "year",
        "incidence_per_1000",
        "low",
        "high",
        "malaria_burden_category",
        "incidence_yoy_change",
    ]
    incidence = master[[c for c in incidence_cols if c in master.columns]].copy()
    incidence.to_sql("malaria_incidence", conn, if_exists="replace", index=False)
    LOGGER.info("Loaded %d rows into malaria_incidence", len(incidence))

    deaths_cols = [
        "country_code",
        "country_name",
        "region",
        "year",
        "deaths_count",
        "deaths_low",
        "deaths_high",
    ]
    deaths = master[[c for c in deaths_cols if c in master.columns]].copy()
    deaths = deaths.dropna(subset=["deaths_count"])
    deaths.to_sql("malaria_deaths", conn, if_exists="replace", index=False)
    LOGGER.info("Loaded %d rows into malaria_deaths", len(deaths))


def load_health_indicators(conn: sqlite3.Connection, processed_dir: str) -> None:
    """Load the health-indicators data into its table.

    Args:
        conn: An open SQLite connection.
        processed_dir: Directory containing processed CSVs.
    """
    health = _read_processed(processed_dir, "health_indicators.csv")
    health.to_sql("health_indicators", conn, if_exists="replace", index=False)
    LOGGER.info("Loaded %d rows into health_indicators", len(health))


def load(config_path: Optional[str] = None) -> None:
    """Run the full load: create schema and populate all tables.

    Args:
        config_path: Optional path to the configuration file.
    """
    config = load_config(config_path)
    paths = get_paths(config)
    db_path = paths["db"]
    processed_dir = paths["processed"]

    conn = sqlite3.connect(db_path)
    try:
        # to_sql with if_exists="replace" drops the typed schema, so re-apply the
        # DDL + indexes after loading to guarantee indexes and metadata exist.
        load_country_metadata_first = True
        create_schema(conn)
        if load_country_metadata_first:
            load_country_metadata(conn)
        load_malaria(conn, processed_dir)
        load_health_indicators(conn, processed_dir)
        # Re-assert indexes (tables replaced by to_sql lose their indexes).
        cur = conn.cursor()
        for idx in INDEXES:
            cur.execute(idx)
        conn.commit()
        LOGGER.info("Load complete. Database at %s", db_path)
    finally:
        conn.close()


if __name__ == "__main__":
    load()
