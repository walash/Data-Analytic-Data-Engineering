"""Loading module for the AI-Driven Climate & Environmental Monitoring pipeline.

Loads the processed CSVs from ``data/processed/`` into the SQLite database
``data/climate_monitoring.db`` with three tables:

    climate_indicators - one row per country-year (wide indicators + features)
    country_profiles   - one row per country with latest values and changes
    ml_predictions     - LinearRegression renewable-energy forecasts

Tables are created with ``CREATE TABLE IF NOT EXISTS`` and fully refreshed on
every run (DELETE + INSERT inside a single transaction), so the stage is
idempotent.

Usage:
    python src/loading/load.py
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

SCHEMA = {
    "climate_indicators": """
        CREATE TABLE IF NOT EXISTS climate_indicators (
            country_code              TEXT    NOT NULL,
            country_name              TEXT    NOT NULL,
            region                    TEXT    NOT NULL,
            sub_region                TEXT,
            year                      INTEGER NOT NULL,
            forest_area_pct           REAL,
            renewable_energy_pct      REAL,
            agricultural_land_pct     REAL,
            electric_power_kwh_pc     REAL,
            electricity_access_pct    REAL,
            renewable_energy_trend    REAL,
            forest_loss_rate          REAL,
            electricity_access_change REAL,
            agricultural_land_change  REAL,
            PRIMARY KEY (country_code, year)
        )
    """,
    "country_profiles": """
        CREATE TABLE IF NOT EXISTS country_profiles (
            country_code                      TEXT PRIMARY KEY,
            country_name                      TEXT NOT NULL,
            region                            TEXT NOT NULL,
            sub_region                        TEXT,
            first_year                        INTEGER,
            last_year                         INTEGER,
            latest_forest_area_pct            REAL,
            latest_forest_area_pct_year       INTEGER,
            change_forest_area_pct            REAL,
            latest_renewable_energy_pct       REAL,
            latest_renewable_energy_pct_year  INTEGER,
            change_renewable_energy_pct       REAL,
            latest_agricultural_land_pct      REAL,
            latest_agricultural_land_pct_year INTEGER,
            change_agricultural_land_pct      REAL,
            latest_electric_power_kwh_pc      REAL,
            latest_electric_power_kwh_pc_year INTEGER,
            change_electric_power_kwh_pc      REAL,
            latest_electricity_access_pct     REAL,
            latest_electricity_access_pct_year INTEGER,
            change_electricity_access_pct     REAL,
            avg_forest_loss_rate              REAL
        )
    """,
    "ml_predictions": """
        CREATE TABLE IF NOT EXISTS ml_predictions (
            country_code                   TEXT    NOT NULL,
            country_name                   TEXT    NOT NULL,
            region                         TEXT    NOT NULL,
            forecast_year                  INTEGER NOT NULL,
            predicted_renewable_energy_pct REAL    NOT NULL,
            last_actual_year               INTEGER,
            last_actual_value              REAL,
            trend_slope_pp_per_year        REAL,
            r2_score                       REAL,
            mae                            REAL,
            training_points                INTEGER,
            model_name                     TEXT,
            PRIMARY KEY (country_code, forecast_year)
        )
    """,
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ci_region_year "
    "ON climate_indicators (region, year)",
    "CREATE INDEX IF NOT EXISTS idx_ci_year ON climate_indicators (year)",
    "CREATE INDEX IF NOT EXISTS idx_ml_year ON ml_predictions (forecast_year)",
]

SOURCE_FILES = {
    "climate_indicators": "climate_indicators_wide.csv",
    "country_profiles": "country_profiles.csv",
    "ml_predictions": "ml_predictions.csv",
}

logger = logging.getLogger("load")


def load_config(path=None):
    """Load the YAML configuration.

    Args:
        path: Optional explicit path; defaults to ``CONFIG_PATH`` env var or
            ``config/config.yaml``.

    Returns:
        dict: Parsed configuration (empty if the file is missing).
    """
    config_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        logger.warning("Config file %s not found; using defaults", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def setup_logging(config):
    """Configure logging using the ``logging`` section of the config.

    Args:
        config: Parsed configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get(
            "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ),
    )


def create_schema(conn):
    """Create all tables and indexes if they do not exist.

    Args:
        conn: Open SQLite connection.
    """
    for table, ddl in SCHEMA.items():
        conn.execute(ddl)
        logger.info("Ensured table %s", table)
    for ddl in INDEXES:
        conn.execute(ddl)
    conn.commit()


def table_columns(conn, table):
    """Return the ordered column names of a table.

    Args:
        conn: Open SQLite connection.
        table: Table name.

    Returns:
        list[str]: Column names.
    """
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def load_table(conn, table, csv_path):
    """Replace the contents of ``table`` with rows from ``csv_path``.

    Only columns present in the table schema are inserted; NaN values become
    SQL NULL.

    Args:
        conn: Open SQLite connection.
        table: Destination table name.
        csv_path: Processed CSV path.

    Returns:
        int: Number of rows inserted.

    Raises:
        FileNotFoundError: If the CSV does not exist.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Processed file not found: {csv_path}")
    frame = pd.read_csv(csv_path)
    columns = [c for c in table_columns(conn, table) if c in frame.columns]
    frame = frame[columns].astype(object).where(frame[columns].notna(), None)
    param_marks = ", ".join("?" for _ in columns)
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) "
           f"VALUES ({param_marks})")
    with conn:
        conn.execute(f"DELETE FROM {table}")
        conn.executemany(sql, frame.itertuples(index=False, name=None))
    logger.info("Loaded %d rows into %s", len(frame), table)
    return len(frame)


def load(config):
    """Run the loading stage.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        dict: Mapping of table name -> row count loaded.
    """
    paths = config.get("paths", {})
    processed_dir = PROJECT_ROOT / paths.get("processed_data",
                                             "data/processed")
    db_path = PROJECT_ROOT / os.getenv(
        "DB_PATH", paths.get("database", "data/climate_monitoring.db"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Loading processed data into %s", db_path)

    summary = {}
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        for table, filename in SOURCE_FILES.items():
            summary[table] = load_table(conn, table, processed_dir / filename)
    finally:
        conn.close()
    return summary


def main():
    """Command-line entry point.

    Returns:
        int: Process exit code.
    """
    config = load_config()
    setup_logging(config)
    try:
        summary = load(config)
    except (FileNotFoundError, sqlite3.Error) as exc:
        logger.error("Loading failed: %s", exc)
        return 1
    logger.info("Loading complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
