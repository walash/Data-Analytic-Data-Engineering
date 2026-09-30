"""Loading module for the Maternal & Child Health (Africa vs USA) pipeline.

Creates the SQLite schema and loads the processed CSVs produced by
``src/transformation/transform.py`` into three tables:

    country_metadata               one row per country (dimension)
    maternal_child_health_metrics  one row per country-year (fact)
    yearly_summary                 one row per region-year (aggregate)

The load is idempotent: tables are dropped and recreated on each run inside a
single transaction, so a failure leaves the previous database intact.

Usage:
    python src/loading/load.py
    DB_PATH=/tmp/mch.db python src/loading/load.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA: Dict[str, str] = {
    "country_metadata": """
        CREATE TABLE country_metadata (
            country_code  TEXT PRIMARY KEY,
            iso2_code     TEXT NOT NULL UNIQUE,
            country_name  TEXT NOT NULL,
            region        TEXT NOT NULL CHECK (region IN ('Africa', 'USA')),
            subregion     TEXT,
            is_africa     INTEGER NOT NULL CHECK (is_africa IN (0, 1))
        )
    """,
    "maternal_child_health_metrics": """
        CREATE TABLE maternal_child_health_metrics (
            country_code                       TEXT    NOT NULL,
            country_name                       TEXT    NOT NULL,
            region                             TEXT    NOT NULL,
            subregion                          TEXT,
            year                               INTEGER NOT NULL,
            maternal_mortality_ratio           REAL,
            under5_mortality_rate              REAL,
            neonatal_mortality_rate            REAL,
            skilled_birth_attendance_pct       REAL,
            health_expenditure_per_capita_usd  REAL,
            immunization_dpt_pct               REAL,
            fertility_rate                     REAL,
            imputed_values_count               INTEGER DEFAULT 0,
            neonatal_share_of_under5_pct       REAL,
            post_neonatal_under5_rate          REAL,
            meets_sdg_mmr                      INTEGER,
            meets_sdg_u5mr                     INTEGER,
            meets_sdg_nmr                      INTEGER,
            mmr_yoy_change_pct                 REAL,
            u5mr_yoy_change_pct                REAL,
            PRIMARY KEY (country_code, year),
            FOREIGN KEY (country_code) REFERENCES country_metadata (country_code)
        )
    """,
    "yearly_summary": """
        CREATE TABLE yearly_summary (
            region                                 TEXT    NOT NULL,
            year                                   INTEGER NOT NULL,
            avg_maternal_mortality_ratio           REAL,
            avg_under5_mortality_rate              REAL,
            avg_neonatal_mortality_rate            REAL,
            avg_skilled_birth_attendance_pct       REAL,
            avg_health_expenditure_per_capita_usd  REAL,
            avg_immunization_dpt_pct               REAL,
            avg_fertility_rate                     REAL,
            countries_reporting_mmr                INTEGER,
            median_maternal_mortality_ratio        REAL,
            PRIMARY KEY (region, year)
        )
    """,
}

INDEXES: List[str] = [
    "CREATE INDEX IF NOT EXISTS idx_metrics_region_year "
    "ON maternal_child_health_metrics (region, year)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_year ON maternal_child_health_metrics (year)",
    "CREATE INDEX IF NOT EXISTS idx_summary_year ON yearly_summary (year)",
]

# Load order respects the foreign key from metrics -> metadata.
LOAD_ORDER = ["country_metadata", "maternal_child_health_metrics", "yearly_summary"]
SOURCE_FILES = {
    "country_metadata": "country_metadata.csv",
    "maternal_child_health_metrics": "maternal_child_health_wide.csv",
    "yearly_summary": "yearly_summary.csv",
}


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Load YAML configuration (``CONFIG_PATH`` env var overrides ``path``)."""
    cfg_path = Path(os.environ.get("CONFIG_PATH", path))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve processed-data directory and database path (env vars take precedence)."""
    processed = Path(os.environ.get("PROCESSED_DATA_DIR", config["paths"]["processed_dir"]))
    db_path = Path(os.environ.get("DB_PATH", config["paths"]["database"]))
    if not processed.is_absolute():
        processed = PROJECT_ROOT / processed
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    return {"processed": processed, "db": db_path}


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Return the column names of a table in declaration order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all tables (children first) plus indexes."""
    for table in reversed(LOAD_ORDER):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for table in LOAD_ORDER:
        conn.execute(SCHEMA[table])
    for ddl in INDEXES:
        conn.execute(ddl)
    logger.info("Schema created: %s", ", ".join(LOAD_ORDER))


def read_processed(path: Path) -> pd.DataFrame:
    """Read a processed CSV, failing loudly if it is missing or empty."""
    if not path.exists():
        raise FileNotFoundError(f"Processed file not found: {path}. Run transform.py first.")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Processed file is empty: {path}")
    return df


def insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert a DataFrame into ``table`` using only the table's declared columns.

    NaN values are converted to SQL NULL; nullable integer flags are cast to
    native Python ints so SQLite stores them as INTEGER.
    """
    cols = table_columns(conn, table)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table}: processed data missing columns {missing}")
    frame = df[cols].astype(object).where(df[cols].notna(), None)
    records = [
        tuple(int(v) if isinstance(v, float) and c in _int_cols(table) else v
              for c, v in zip(cols, row))
        for row in frame.itertuples(index=False, name=None)
    ]
    param_marks = ", ".join("?" for _ in cols)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({param_marks})", records
    )
    return len(records)


def _int_cols(table: str) -> set:
    """Return INTEGER-typed columns for a table based on the DDL."""
    ints = set()
    for line in SCHEMA[table].splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].rstrip(",") == "INTEGER":
            ints.add(parts[0])
    return ints


def validate(conn: sqlite3.Connection) -> Dict[str, int]:
    """Run post-load integrity checks and return table row counts."""
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in LOAD_ORDER}
    orphans = conn.execute(
        "SELECT COUNT(*) FROM maternal_child_health_metrics m "
        "LEFT JOIN country_metadata c USING (country_code) WHERE c.country_code IS NULL"
    ).fetchone()[0]
    if orphans:
        raise ValueError(f"{orphans} metric rows reference unknown countries")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise ValueError(f"Foreign key violations: {fk_errors[:5]}")
    return counts


def load(config: Dict[str, Any]) -> Dict[str, int]:
    """Create schema and load all processed tables. Returns row counts per table."""
    paths = get_paths(config)
    paths["db"].parent.mkdir(parents=True, exist_ok=True)
    frames = {t: read_processed(paths["processed"] / SOURCE_FILES[t]) for t in LOAD_ORDER}

    conn = sqlite3.connect(paths["db"])
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:  # single transaction: commit on success, rollback on error
            create_schema(conn)
            for table in LOAD_ORDER:
                n = insert_frame(conn, table, frames[table])
                logger.info("Loaded %4d rows -> %s", n, table)
        counts = validate(conn)
    finally:
        conn.close()
    logger.info("Database ready at %s: %s", paths["db"], counts)
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Load processed data into SQLite.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.yaml")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        load(load_config(Path(args.config)))
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Load failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
