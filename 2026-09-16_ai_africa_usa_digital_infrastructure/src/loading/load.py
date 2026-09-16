"""Loading module for the AI Adoption & Digital Infrastructure pipeline.

Reads the processed CSVs produced by :mod:`src.transformation.transform` and
loads them into a SQLite database at ``data/digital_infrastructure.db``.

Tables
------
* ``digital_metrics`` - full country-year panel of all indicators plus derived
  columns (from ``digital_infrastructure.csv``).
* ``country_summary`` - latest-year headline metrics per country with region
  labels (from ``country_summary.csv``), used for regional aggregation.

Helpful indexes are created on the natural keys and on the region column to keep
the analytical queries in ``sql/queries.sql`` fast.

Run directly (``python src/loading/load.py``) or import :func:`load` from an
orchestrator.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

LOGGER = logging.getLogger("loading")

SCHEMA: Dict[str, str] = {
    "digital_metrics": """
        CREATE TABLE digital_metrics (
            country_code                TEXT    NOT NULL,
            country_name                TEXT,
            region                      TEXT,
            year                        INTEGER NOT NULL,
            internet_users_pct          REAL,
            mobile_subscriptions_per100 REAL,
            fixed_broadband_per100      REAL,
            hightech_exports_pct        REAL,
            rnd_expenditure_pct         REAL,
            internet_mobile_ratio       REAL,
            norm_internet               REAL,
            norm_broadband              REAL,
            norm_mobile                 REAL,
            digital_readiness_score     REAL,
            PRIMARY KEY (country_code, year)
        )
    """,
    "country_summary": """
        CREATE TABLE country_summary (
            country_code                TEXT    NOT NULL PRIMARY KEY,
            country_name                TEXT,
            region                      TEXT,
            year                        INTEGER,
            internet_users_pct          REAL,
            mobile_subscriptions_per100 REAL,
            fixed_broadband_per100      REAL,
            hightech_exports_pct        REAL,
            rnd_expenditure_pct         REAL,
            internet_mobile_ratio       REAL,
            digital_readiness_score     REAL
        )
    """,
}

INDEXES = [
    "CREATE INDEX idx_metrics_region ON digital_metrics(region)",
    "CREATE INDEX idx_metrics_year ON digital_metrics(year)",
    "CREATE INDEX idx_summary_region ON country_summary(region)",
]


def _configure_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format.

    Parameters
    ----------
    level:
        Logging level name.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return Path(__file__).resolve().parents[2]


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and parse the YAML configuration file.

    Parameters
    ----------
    config_path:
        Optional explicit path to ``config.yaml``.

    Returns
    -------
    dict
        Parsed configuration mapping.

    Raises
    ------
    FileNotFoundError
        If the configuration file cannot be located.
    """
    if config_path is None:
        config_path = os.environ.get(
            "CONFIG_PATH", str(project_root() / "config" / "config.yaml")
        )
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all tables defined in :data:`SCHEMA`.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    """
    cursor = conn.cursor()
    for table, ddl in SCHEMA.items():
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
        cursor.execute(ddl)
        LOGGER.info("Created table %s", table)
    conn.commit()


def _read_processed(processed_dir: Path, name: str) -> pd.DataFrame:
    """Read a processed CSV into a DataFrame.

    Parameters
    ----------
    processed_dir:
        Directory containing processed CSVs.
    name:
        File name without extension.

    Returns
    -------
    pandas.DataFrame
        Loaded frame.

    Raises
    ------
    FileNotFoundError
        If the expected file is missing.
    """
    path = processed_dir / f"{name}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Processed file missing: {path} (run transform first)")
    frame = pd.read_csv(path)
    LOGGER.info("Read %s (%d rows)", path.name, len(frame))
    return frame


def load_table(conn: sqlite3.Connection, frame: pd.DataFrame, table: str) -> int:
    """Load a DataFrame into an existing table, aligning to its columns.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    frame:
        Source data.
    table:
        Destination table name (must already exist).

    Returns
    -------
    int
        Number of rows inserted.
    """
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    usable = [col for col in existing if col in frame.columns]
    subset = frame[usable].copy()
    subset = subset.where(pd.notnull(subset), None)
    subset.to_sql(table, conn, if_exists="append", index=False)
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    LOGGER.info("Loaded %d rows into %s", count, table)
    return count


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create analytical indexes defined in :data:`INDEXES`.

    Parameters
    ----------
    conn:
        Open SQLite connection.
    """
    cursor = conn.cursor()
    for statement in INDEXES:
        cursor.execute(statement)
    conn.commit()
    LOGGER.info("Created %d indexes", len(INDEXES))


def load(config_path: Optional[str] = None) -> Dict[str, int]:
    """Run the full loading stage and populate the SQLite database.

    Parameters
    ----------
    config_path:
        Optional path to the configuration file.

    Returns
    -------
    dict
        Mapping of table name to row count.
    """
    config = load_config(config_path)
    _configure_logging(config.get("pipeline", {}).get("log_level", "INFO"))

    processed_dir = project_root() / config["paths"]["processed_data"]
    db_path = project_root() / config["paths"]["database"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    metrics = _read_processed(processed_dir, "digital_infrastructure")
    summary = _read_processed(processed_dir, "country_summary")

    LOGGER.info("Opening database at %s", db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        create_schema(conn)
        counts = {
            "digital_metrics": load_table(conn, metrics, "digital_metrics"),
            "country_summary": load_table(conn, summary, "country_summary"),
        }
        create_indexes(conn)
        conn.commit()
    finally:
        conn.close()

    LOGGER.info("Loading complete: %s", counts)
    return counts


def main() -> None:
    """CLI / Airflow entry point that runs :func:`load`."""
    load()


if __name__ == "__main__":
    main()
