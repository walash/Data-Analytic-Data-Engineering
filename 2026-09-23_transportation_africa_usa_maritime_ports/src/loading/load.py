"""Loading module for the Africa-USA Maritime Port Trade project.

This module loads the processed master dataset into a SQLite database at
``data/maritime_ports.db``. It creates three tables:

    * ``maritime_indicators`` - the full merged/derived dataset, one row per
      country-year.
    * ``country_metadata``    - static metadata (country, iso3, region,
      sub_region) derived from the master dataset.
    * ``yearly_summary``      - metrics aggregated by year and region.

Indexes are created on the ``country`` and ``year`` columns of the main table
to keep analytical queries fast.

Usage:
    python src/loading/load.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Dict, Optional

import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("load")

# --------------------------------------------------------------------------- #
# SQL schema definitions
# --------------------------------------------------------------------------- #
SCHEMA: Dict[str, str] = {
    "maritime_indicators": """
        CREATE TABLE IF NOT EXISTS maritime_indicators (
            country                  TEXT,
            iso3                     TEXT,
            year                     INTEGER,
            liner_shipping_index     REAL,
            container_throughput_teu REAL,
            merchandise_trade_pct_gdp REAL,
            lpi_score                REAL,
            exports_usd              REAL,
            region                   TEXT,
            sub_region               TEXT,
            shipping_trade_ratio     REAL,
            port_efficiency_score    REAL,
            PRIMARY KEY (iso3, year)
        )
    """,
    "country_metadata": """
        CREATE TABLE IF NOT EXISTS country_metadata (
            country    TEXT,
            iso3       TEXT PRIMARY KEY,
            region     TEXT,
            sub_region TEXT
        )
    """,
    "yearly_summary": """
        CREATE TABLE IF NOT EXISTS yearly_summary (
            year                      INTEGER,
            region                    TEXT,
            avg_liner_shipping_index  REAL,
            total_container_teu       REAL,
            avg_merchandise_trade_pct REAL,
            avg_lpi_score             REAL,
            total_exports_usd         REAL,
            country_count             INTEGER,
            PRIMARY KEY (year, region)
        )
    """,
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_indicators_country "
    "ON maritime_indicators(country)",
    "CREATE INDEX IF NOT EXISTS idx_indicators_year "
    "ON maritime_indicators(year)",
    "CREATE INDEX IF NOT EXISTS idx_indicators_iso3 "
    "ON maritime_indicators(iso3)",
    "CREATE INDEX IF NOT EXISTS idx_summary_year "
    "ON yearly_summary(year)",
]


def _project_root() -> str:
    """Return the absolute path to the project root directory.

    Returns:
        str: Absolute path of the project root (two levels up from this file).
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def load_config() -> Optional[dict]:
    """Load ``config/config.yaml`` when available.

    Returns:
        Optional[dict]: Parsed configuration, or ``None`` if unavailable.
    """
    config_path = os.path.join(_project_root(), "config", "config.yaml")
    if yaml is None or not os.path.exists(config_path):
        return None
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def get_paths(config: Optional[dict]) -> Dict[str, str]:
    """Resolve the processed data directory and database path.

    Args:
        config: Optional configuration dictionary.

    Returns:
        Dict[str, str]: Mapping with ``processed`` and ``database`` absolute
        paths. The database's parent directory is created if needed.
    """
    proc_rel = "data/processed"
    db_rel = "data/maritime_ports.db"
    if config:
        paths = config.get("paths", {})
        proc_rel = paths.get("processed_data_dir", proc_rel)
        db_rel = paths.get("database_path", db_rel)
    root = _project_root()
    processed = os.path.join(root, proc_rel)
    database = os.path.join(root, db_rel)
    os.makedirs(os.path.dirname(database), exist_ok=True)
    return {"processed": processed, "database": database}


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all database tables if they do not already exist.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for table_name, ddl in SCHEMA.items():
        cursor.execute(ddl)
        LOGGER.info("Ensured table exists: %s", table_name)
    conn.commit()


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create analytical indexes on the main tables.

    Args:
        conn: Open SQLite connection.
    """
    cursor = conn.cursor()
    for statement in INDEXES:
        cursor.execute(statement)
    conn.commit()
    LOGGER.info("Created %d indexes.", len(INDEXES))


def _load_master(processed_dir: str) -> pd.DataFrame:
    """Read the processed master dataset.

    Args:
        processed_dir: Absolute path to the processed data directory.

    Returns:
        pd.DataFrame: The master maritime dataset.

    Raises:
        FileNotFoundError: If the master CSV is missing.
    """
    master_path = os.path.join(processed_dir, "master_maritime_data.csv")
    if not os.path.exists(master_path):
        raise FileNotFoundError(
            f"Master dataset not found: {master_path}. "
            "Run the transformation stage first."
        )
    frame = pd.read_csv(master_path)
    LOGGER.info("Loaded master dataset (%d rows) from %s", len(frame), master_path)
    return frame


def load_maritime_indicators(
    conn: sqlite3.Connection, master: pd.DataFrame
) -> int:
    """Load the master dataset into the ``maritime_indicators`` table.

    Args:
        conn: Open SQLite connection.
        master: Master maritime DataFrame.

    Returns:
        int: Number of rows written.
    """
    columns = [
        "country", "iso3", "year", "liner_shipping_index",
        "container_throughput_teu", "merchandise_trade_pct_gdp", "lpi_score",
        "exports_usd", "region", "sub_region", "shipping_trade_ratio",
        "port_efficiency_score",
    ]
    frame = master.reindex(columns=columns)
    frame.to_sql(
        "maritime_indicators", conn, if_exists="replace", index=False,
    )
    LOGGER.info("Loaded %d rows into maritime_indicators.", len(frame))
    return len(frame)


def load_country_metadata(
    conn: sqlite3.Connection, master: pd.DataFrame
) -> int:
    """Load distinct country metadata into ``country_metadata``.

    Args:
        conn: Open SQLite connection.
        master: Master maritime DataFrame.

    Returns:
        int: Number of metadata rows written.
    """
    meta = (
        master[["country", "iso3", "region", "sub_region"]]
        .dropna(subset=["iso3"])
        .drop_duplicates(subset=["iso3"], keep="last")
        .sort_values("iso3")
        .reset_index(drop=True)
    )
    meta.to_sql("country_metadata", conn, if_exists="replace", index=False)
    LOGGER.info("Loaded %d rows into country_metadata.", len(meta))
    return len(meta)


def load_yearly_summary(
    conn: sqlite3.Connection, master: pd.DataFrame
) -> int:
    """Aggregate the master dataset by year and region and load the summary.

    Args:
        conn: Open SQLite connection.
        master: Master maritime DataFrame.

    Returns:
        int: Number of summary rows written.
    """
    grouped = master.groupby(["year", "region"], dropna=False)
    summary = grouped.agg(
        avg_liner_shipping_index=("liner_shipping_index", "mean"),
        total_container_teu=("container_throughput_teu", "sum"),
        avg_merchandise_trade_pct=("merchandise_trade_pct_gdp", "mean"),
        avg_lpi_score=("lpi_score", "mean"),
        total_exports_usd=("exports_usd", "sum"),
        country_count=("iso3", "nunique"),
    ).reset_index()

    summary["year"] = summary["year"].astype(int)
    summary.to_sql("yearly_summary", conn, if_exists="replace", index=False)
    LOGGER.info("Loaded %d rows into yearly_summary.", len(summary))
    return len(summary)


def load() -> str:
    """Run the full loading stage end-to-end.

    Creates the schema, loads all three tables from the processed master
    dataset and builds indexes.

    Returns:
        str: Absolute path of the SQLite database written.
    """
    config = load_config()
    paths = get_paths(config)
    master = _load_master(paths["processed"])

    conn = sqlite3.connect(paths["database"])
    try:
        create_schema(conn)
        load_maritime_indicators(conn, master)
        load_country_metadata(conn, master)
        load_yearly_summary(conn, master)
        create_indexes(conn)
    finally:
        conn.close()

    LOGGER.info("Loading complete. Database at %s", paths["database"])
    return paths["database"]


def main() -> None:
    """Command-line entry point for the loading stage."""
    LOGGER.info("Starting loading stage ...")
    load()
    LOGGER.info("Loading stage finished.")


if __name__ == "__main__":
    main()
