"""Loading module for the EdTech & Learning Analytics pipeline.

Creates the SQLite database ``data/education_analytics.db`` and loads
the processed CSVs produced by ``src/transformation/transform.py``:

    raw_indicators    <- data/processed/raw_indicators_clean.csv
    edtech_readiness  <- data/processed/education_analytics.csv
    country_summary   <- data/processed/country_summary.csv

Tables are created with ``CREATE TABLE IF NOT EXISTS`` and fully
refreshed on every run so the load is idempotent.
"""

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA = {
    "raw_indicators": """
        CREATE TABLE IF NOT EXISTS raw_indicators (
            country_id      TEXT    NOT NULL,
            country_name    TEXT    NOT NULL,
            region          TEXT    NOT NULL,
            indicator_id    TEXT    NOT NULL,
            indicator_key   TEXT    NOT NULL,
            indicator_name  TEXT,
            year            INTEGER NOT NULL,
            value           REAL    NOT NULL,
            PRIMARY KEY (country_id, indicator_id, year)
        )""",
    "edtech_readiness": """
        CREATE TABLE IF NOT EXISTS edtech_readiness (
            country_id                     TEXT    NOT NULL,
            country_name                   TEXT    NOT NULL,
            region                         TEXT    NOT NULL,
            year                           INTEGER NOT NULL,
            primary_completion_rate        REAL,
            education_expenditure_pct_gdp  REAL,
            tertiary_enrollment_rate       REAL,
            internet_users_pct             REAL,
            youth_literacy_rate            REAL,
            edtech_readiness_score         REAL,
            readiness_components           INTEGER,
            internet_users_yoy_change      REAL,
            digital_learning_gap_vs_usa    REAL,
            PRIMARY KEY (country_id, year)
        )""",
    "country_summary": """
        CREATE TABLE IF NOT EXISTS country_summary (
            country_id                          TEXT PRIMARY KEY,
            country_name                        TEXT NOT NULL,
            region                              TEXT NOT NULL,
            primary_completion_rate             REAL,
            education_expenditure_pct_gdp       REAL,
            tertiary_enrollment_rate            REAL,
            internet_users_pct                  REAL,
            youth_literacy_rate                 REAL,
            primary_completion_rate_year        INTEGER,
            education_expenditure_pct_gdp_year  INTEGER,
            tertiary_enrollment_rate_year       INTEGER,
            internet_users_pct_year             INTEGER,
            youth_literacy_rate_year            INTEGER,
            internet_growth_pp                  REAL,
            edtech_readiness_score              REAL,
            readiness_components                INTEGER,
            readiness_rank                      INTEGER,
            observation_count                   INTEGER,
            first_year                          INTEGER,
            last_year                           INTEGER
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_raw_indicator_year "
    "ON raw_indicators (indicator_key, year)",
    "CREATE INDEX IF NOT EXISTS idx_raw_region "
    "ON raw_indicators (region)",
    "CREATE INDEX IF NOT EXISTS idx_readiness_year "
    "ON edtech_readiness (year)",
]

SOURCES = {
    "raw_indicators": "raw_indicators_clean.csv",
    "edtech_readiness": "education_analytics.csv",
    "country_summary": "country_summary.csv",
}


def load_config(path=None):
    """Load the YAML configuration file.

    Args:
        path: Optional config path; defaults to ``CONFIG_PATH`` env var
            or ``config/config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def create_schema(conn):
    """Create all tables and indexes if they do not already exist.

    Args:
        conn: Open ``sqlite3.Connection``.
    """
    cur = conn.cursor()
    for name, ddl in SCHEMA.items():
        cur.execute(ddl)
        logger.info("Ensured table %s", name)
    for ddl in INDEXES:
        cur.execute(ddl)
    conn.commit()


def table_columns(conn, table):
    """Return the column names defined for a table.

    Args:
        conn: Open ``sqlite3.Connection``.
        table: Table name.

    Returns:
        list[str]: Column names in schema order.
    """
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def load_table(conn, table, csv_path, batch_size=500):
    """Replace a table's contents with the rows of a processed CSV.

    Only columns present in the table schema are inserted; NaN values
    become SQL NULLs.

    Args:
        conn: Open ``sqlite3.Connection``.
        table: Destination table name.
        csv_path: Path to the processed CSV file.
        batch_size: Number of rows per ``executemany`` batch.

    Returns:
        int: Number of rows inserted.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"{csv_path} not found - run the transformation stage first")
    df = pd.read_csv(csv_path)
    cols = [c for c in table_columns(conn, table) if c in df.columns]
    df = df[cols].astype(object).where(df[cols].notna(), None)
    value_slots = ", ".join("?" for _ in cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
           f"VALUES ({value_slots})")
    records = [tuple(r) for r in df.itertuples(index=False, name=None)]
    conn.execute(f"DELETE FROM {table}")
    for start in range(0, len(records), batch_size):
        conn.executemany(sql, records[start:start + batch_size])
    conn.commit()
    logger.info("Loaded %d rows into %s", len(records), table)
    return len(records)


def load(config=None):
    """Run the full loading stage.

    Args:
        config: Configuration dict (loaded from disk if omitted).

    Returns:
        dict: Row counts per table.
    """
    config = config or load_config()
    processed = PROJECT_ROOT / config["paths"]["processed_data_dir"]
    db_path = PROJECT_ROOT / config["paths"]["database_path"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    batch_size = config.get("pipeline", {}).get("batch_size", 500)
    counts = {}
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        for table, filename in SOURCES.items():
            counts[table] = load_table(conn, table, processed / filename,
                                       batch_size)
    logger.info("Database ready at %s: %s", db_path, counts)
    return counts


def main():
    """Command-line entry point.

    Returns:
        int: Process exit code.
    """
    config = load_config()
    log_cfg = config.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, os.environ.get(
            "LOG_LEVEL", log_cfg.get("level", "INFO")).upper(), logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
    )
    try:
        load(config)
    except (FileNotFoundError, sqlite3.Error) as exc:
        logger.error("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
