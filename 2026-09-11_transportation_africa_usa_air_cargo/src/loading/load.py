"""Data loading module for the Africa-USA Air Cargo project.

This module reads the processed CSV files produced by
``src/transformation/transform.py`` and loads them into a local SQLite database
(``data/air_cargo.db``). It creates four analytical tables:

    * ``airports``            -- African and US reference airports.
    * ``routes``              -- Africa <-> USA air routes.
    * ``air_freight_stats``   -- World Bank air-freight observations.
    * ``air_passenger_stats`` -- World Bank air-passenger observations.

The SQLite database is the query target for ``sql/queries.sql`` and the
analysis notebook.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("load")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# DDL statements for each table. Executed before data is inserted.
SCHEMA = {
    "airports": """
        CREATE TABLE IF NOT EXISTS airports (
            ident             TEXT,
            type              TEXT,
            name              TEXT,
            latitude_deg      REAL,
            longitude_deg     REAL,
            elevation_ft      REAL,
            continent         TEXT,
            iso_country       TEXT,
            iso_region        TEXT,
            municipality      TEXT,
            scheduled_service TEXT,
            icao_code         TEXT,
            iata_code         TEXT,
            region            TEXT
        )
    """,
    "routes": """
        CREATE TABLE IF NOT EXISTS routes (
            airline        TEXT,
            airline_id     TEXT,
            src_airport    TEXT,
            src_airport_id TEXT,
            dst_airport    TEXT,
            dst_airport_id TEXT,
            codeshare      TEXT,
            stops          INTEGER,
            equipment      TEXT,
            direction      TEXT
        )
    """,
    "air_freight_stats": """
        CREATE TABLE IF NOT EXISTS air_freight_stats (
            country       TEXT,
            country_iso3  TEXT,
            year          INTEGER,
            freight_mt_km REAL,
            region        TEXT
        )
    """,
    "air_passenger_stats": """
        CREATE TABLE IF NOT EXISTS air_passenger_stats (
            country      TEXT,
            country_iso3 TEXT,
            year         INTEGER,
            passengers   REAL,
            region       TEXT
        )
    """,
}


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the YAML project configuration.

    Args:
        config_path: Path to ``config.yaml``.

    Returns:
        Parsed configuration dictionary (empty dict on failure).
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning("Could not load config (%s); using defaults.", exc)
        return {}


def get_paths(config: dict) -> tuple[Path, Path]:
    """Resolve the processed data directory and the SQLite database path.

    Args:
        config: Parsed project configuration.

    Returns:
        A ``(processed_dir, db_path)`` tuple of absolute paths. The parent
        directory of the database is created if necessary.
    """
    paths = config.get("paths", {})
    processed_dir = (
        PROJECT_ROOT / paths.get("processed", "data/processed")
    ).resolve()
    db_path = (PROJECT_ROOT / paths.get("database", "data/air_cargo.db")).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return processed_dir, db_path


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables defined in :data:`SCHEMA`.

    Existing tables are dropped first so that ``load`` is idempotent.

    Args:
        conn: An open SQLite connection.
    """
    cursor = conn.cursor()
    for table, ddl in SCHEMA.items():
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute(ddl)
        LOGGER.info("Created table '%s'.", table)
    conn.commit()


def _read_csv(path: Path) -> pd.DataFrame:
    """Read a processed CSV file, returning an empty frame if it is missing.

    Args:
        path: Path to the CSV file.

    Returns:
        The loaded DataFrame, or an empty DataFrame when the file is absent.
    """
    if not path.exists():
        LOGGER.warning("Expected processed file missing: %s", path)
        return pd.DataFrame()
    return pd.read_csv(path, low_memory=False)


def load_airports(conn: sqlite3.Connection, processed_dir: Path) -> int:
    """Load African and US airports into the ``airports`` table.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSV files.

    Returns:
        The number of airport rows inserted.
    """
    cols = [
        "ident", "type", "name", "latitude_deg", "longitude_deg",
        "elevation_ft", "continent", "iso_country", "iso_region",
        "municipality", "scheduled_service", "icao_code", "iata_code",
    ]

    frames = []
    for filename, region in (
        ("african_airports.csv", "Africa"),
        ("us_airports.csv", "USA"),
    ):
        df = _read_csv(processed_dir / filename)
        if df.empty:
            continue
        keep = [c for c in cols if c in df.columns]
        df = df[keep].copy()
        df["region"] = region
        frames.append(df)

    if not frames:
        LOGGER.warning("No airport data to load.")
        return 0

    combined = pd.concat(frames, ignore_index=True)
    combined.to_sql("airports", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d airport rows.", len(combined))
    return len(combined)


def load_routes(conn: sqlite3.Connection, processed_dir: Path) -> int:
    """Load Africa-USA routes into the ``routes`` table.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSV files.

    Returns:
        The number of route rows inserted.
    """
    df = _read_csv(processed_dir / "africa_usa_routes.csv")
    if df.empty:
        LOGGER.warning("No route data to load.")
        return 0
    df.to_sql("routes", conn, if_exists="append", index=False)
    LOGGER.info("Loaded %d route rows.", len(df))
    return len(df)


def load_worldbank(
    conn: sqlite3.Connection, processed_dir: Path
) -> tuple[int, int]:
    """Load World Bank freight and passenger stats.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSV files.

    Returns:
        A ``(freight_rows, passenger_rows)`` tuple of inserted row counts.
    """
    freight = _read_csv(processed_dir / "air_freight_stats.csv")
    passengers = _read_csv(processed_dir / "air_passenger_stats.csv")

    freight_rows = 0
    if not freight.empty:
        freight.to_sql(
            "air_freight_stats", conn, if_exists="append", index=False
        )
        freight_rows = len(freight)
        LOGGER.info("Loaded %d air-freight rows.", freight_rows)

    passenger_rows = 0
    if not passengers.empty:
        passengers.to_sql(
            "air_passenger_stats", conn, if_exists="append", index=False
        )
        passenger_rows = len(passengers)
        LOGGER.info("Loaded %d air-passenger rows.", passenger_rows)

    return freight_rows, passenger_rows


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create helpful indexes to speed up analytical queries.

    Args:
        conn: Open SQLite connection.
    """
    statements = [
        "CREATE INDEX IF NOT EXISTS idx_airports_country "
        "ON airports (iso_country)",
        "CREATE INDEX IF NOT EXISTS idx_airports_iata "
        "ON airports (iata_code)",
        "CREATE INDEX IF NOT EXISTS idx_routes_src ON routes (src_airport)",
        "CREATE INDEX IF NOT EXISTS idx_routes_dst ON routes (dst_airport)",
        "CREATE INDEX IF NOT EXISTS idx_freight_iso "
        "ON air_freight_stats (country_iso3)",
        "CREATE INDEX IF NOT EXISTS idx_passenger_iso "
        "ON air_passenger_stats (country_iso3)",
    ]
    cursor = conn.cursor()
    for stmt in statements:
        cursor.execute(stmt)
    conn.commit()
    LOGGER.info("Created analytical indexes.")


def load(config: Optional[dict] = None) -> dict:
    """Run the full load pipeline.

    Args:
        config: Optional pre-loaded configuration. When ``None`` the default
            config file is loaded.

    Returns:
        A mapping of table name to the number of rows loaded.
    """
    config = config or load_config()
    processed_dir, db_path = get_paths(config)
    LOGGER.info("Creating SQLite database at %s", db_path)

    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        airport_rows = load_airports(conn, processed_dir)
        route_rows = load_routes(conn, processed_dir)
        freight_rows, passenger_rows = load_worldbank(conn, processed_dir)
        create_indexes(conn)
        conn.commit()
    finally:
        conn.close()

    summary = {
        "airports": airport_rows,
        "routes": route_rows,
        "air_freight_stats": freight_rows,
        "air_passenger_stats": passenger_rows,
    }
    LOGGER.info("Load complete: %s", summary)
    return summary


def main() -> int:
    """Entry point for command-line execution.

    Returns:
        Process exit code (``0`` on success).
    """
    load()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
