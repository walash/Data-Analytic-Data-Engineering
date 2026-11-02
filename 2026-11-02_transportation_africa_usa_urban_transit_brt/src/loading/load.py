"""Loading stage of the Urban Transit & BRT Africa-USA pipeline.

Loads the processed CSVs produced by ``src/transformation/transform.py`` into
a SQLite analytics database (``data/transit_analytics.db`` by default, or the
``DB_PATH`` environment variable). Three tables are (re)created:

* ``brt_systems``      - cleaned BRT inventory, one row per system.
* ``urban_indicators`` - World Bank urban/GDP indicators per country-year.
* ``brt_enriched``     - BRT systems joined with latest urban indicators
  plus derived performance metrics.

Each load is a full refresh inside a transaction, so re-running the pipeline
is idempotent. Indexes supporting the queries in ``sql/queries.sql`` are
created after loading.
"""

import logging
import os
import sqlite3
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("loading")

DDL = {
    "brt_systems": """
        CREATE TABLE IF NOT EXISTS brt_systems (
            system_id           INTEGER PRIMARY KEY,
            city                TEXT    NOT NULL,
            country             TEXT    NOT NULL,
            iso3                TEXT    NOT NULL CHECK (length(iso3) = 3),
            continent           TEXT    NOT NULL,
            system_name         TEXT    NOT NULL,
            year_opened         INTEGER CHECK (year_opened BETWEEN 1950 AND 2100),
            corridor_length_km  REAL    CHECK (corridor_length_km > 0),
            daily_ridership     REAL    CHECK (daily_ridership >= 0),
            num_stations        REAL    CHECK (num_stations > 0),
            fleet_size          REAL    CHECK (fleet_size > 0),
            status              TEXT    DEFAULT 'Unknown',
            operator            TEXT,
            source              TEXT,
            UNIQUE (system_name, city)
        )""",
    "urban_indicators": """
        CREATE TABLE IF NOT EXISTS urban_indicators (
            iso3                   TEXT    NOT NULL CHECK (length(iso3) = 3),
            country_name           TEXT,
            year                   INTEGER NOT NULL,
            urban_population       REAL,
            urban_growth_rate_pct  REAL,
            gdp_per_capita_usd     REAL,
            urban_pct_of_total     REAL CHECK (urban_pct_of_total BETWEEN 0 AND 100),
            PRIMARY KEY (iso3, year)
        )""",
    "brt_enriched": """
        CREATE TABLE IF NOT EXISTS brt_enriched (
            system_id                   INTEGER PRIMARY KEY,
            city                        TEXT NOT NULL,
            country                     TEXT NOT NULL,
            iso3                        TEXT NOT NULL,
            continent                   TEXT NOT NULL,
            system_name                 TEXT NOT NULL,
            year_opened                 INTEGER,
            corridor_length_km          REAL,
            daily_ridership             REAL,
            num_stations                REAL,
            fleet_size                  REAL,
            status                      TEXT,
            operator                    TEXT,
            source                      TEXT,
            wb_year                     INTEGER,
            urban_population            REAL,
            urban_growth_rate_pct       REAL,
            gdp_per_capita_usd          REAL,
            urban_pct_of_total          REAL,
            ridership_per_km            REAL,
            ridership_per_station       REAL,
            fleet_utilization_ratio     REAL,
            km_per_station              REAL,
            system_age_years            REAL,
            ridership_per_1k_urban_pop  REAL,
            FOREIGN KEY (system_id) REFERENCES brt_systems (system_id)
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_brt_continent ON brt_systems (continent)",
    "CREATE INDEX IF NOT EXISTS idx_brt_iso3 ON brt_systems (iso3)",
    "CREATE INDEX IF NOT EXISTS idx_urban_year ON urban_indicators (year)",
    "CREATE INDEX IF NOT EXISTS idx_enriched_continent ON brt_enriched (continent)",
]


def load_config(path=CONFIG_PATH):
    """Load the YAML pipeline configuration.

    Args:
        path: Path to ``config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_paths(config):
    """Resolve processed-data directory and database path.

    ``DATA_DIR`` and ``DB_PATH`` environment variables override config.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        tuple[Path, Path]: ``(processed_dir, db_path)``.
    """
    data_dir = os.getenv("DATA_DIR")
    processed = Path(data_dir) / "processed" if data_dir else PROJECT_ROOT / config["paths"]["processed_data"]
    db_path = Path(os.getenv("DB_PATH", PROJECT_ROOT / config["paths"]["database"]))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return processed, db_path


def create_connection(db_path):
    """Open a SQLite connection with foreign keys enabled.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        sqlite3.Connection: Open connection.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    logger.info("Connected to SQLite database %s", db_path)
    return conn


def create_tables(conn, drop_existing=True):
    """Create the analytics schema (optionally dropping existing tables).

    Args:
        conn: Open SQLite connection.
        drop_existing: Drop tables first for a full refresh.
    """
    with conn:
        if drop_existing:
            for table in ("brt_enriched", "urban_indicators", "brt_systems"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        for table, ddl in DDL.items():
            conn.execute(ddl)
            logger.info("Ensured table %s", table)


def _table_columns(conn, table):
    """Return the ordered column names of a table.

    Args:
        conn: Open SQLite connection.
        table: Table name.

    Returns:
        list[str]: Column names.
    """
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _insert_frame(conn, table, df):
    """Insert a DataFrame into a table, aligning to the table's columns.

    Missing columns are inserted as NULL; pandas NA values become NULL.

    Args:
        conn: Open SQLite connection.
        table: Destination table name.
        df: Data to insert.

    Returns:
        int: Number of rows inserted.
    """
    columns = _table_columns(conn, table)
    aligned = df.reindex(columns=columns).astype(object).where(df.reindex(columns=columns).notna(), None)
    param_marks = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({param_marks})"
    rows = [tuple(r) for r in aligned.itertuples(index=False, name=None)]
    with conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(sql, rows)
    logger.info("Loaded %d rows into %s", len(rows), table)
    return len(rows)


def load_brt_systems(conn, processed_dir):
    """Load ``brt_systems_clean.csv`` into ``brt_systems``.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSVs.

    Returns:
        int: Rows loaded.
    """
    df = pd.read_csv(Path(processed_dir) / "brt_systems_clean.csv")
    df["year_opened"] = pd.to_numeric(df["year_opened"], errors="coerce").astype("Int64")
    return _insert_frame(conn, "brt_systems", df)


def load_urban_indicators(conn, processed_dir):
    """Load ``urban_indicators_clean.csv`` into ``urban_indicators``.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSVs.

    Returns:
        int: Rows loaded.
    """
    df = pd.read_csv(Path(processed_dir) / "urban_indicators_clean.csv")
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    return _insert_frame(conn, "urban_indicators", df)


def load_enriched_data(conn, processed_dir):
    """Load ``brt_enriched.csv`` into ``brt_enriched``.

    Args:
        conn: Open SQLite connection.
        processed_dir: Directory containing processed CSVs.

    Returns:
        int: Rows loaded.
    """
    df = pd.read_csv(Path(processed_dir) / "brt_enriched.csv")
    for col in ("year_opened", "wb_year"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return _insert_frame(conn, "brt_enriched", df)


def run_loading(config=None):
    """Execute the full loading stage.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        dict[str, int]: Row counts per table.
    """
    config = config or load_config()
    processed_dir, db_path = resolve_paths(config)
    conn = create_connection(db_path)
    try:
        create_tables(conn)
        counts = {
            "brt_systems": load_brt_systems(conn, processed_dir),
            "urban_indicators": load_urban_indicators(conn, processed_dir),
            "brt_enriched": load_enriched_data(conn, processed_dir),
        }
        with conn:
            for stmt in INDEXES:
                conn.execute(stmt)
        logger.info("Loading complete: %s", counts)
        return counts
    finally:
        conn.close()


def main():
    """CLI entry point: configure logging and run the loading stage."""
    config = load_config()
    log_cfg = config.get("logging", {})
    logging.basicConfig(level=log_cfg.get("level", "INFO"), format=log_cfg.get("format"))
    for table, count in run_loading(config).items():
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
