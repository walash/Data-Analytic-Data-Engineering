"""Loading stage for the Deepwater Offshore Oil Production project.

Creates a SQLite database (default ``data/oil_production.db``) and loads the
processed CSVs from ``data/processed/`` into two tables:

* ``oil_production`` - country-year production metrics (PK iso_code, year)
* ``oil_rents``      - country-year oil rents and GDP (PK iso_code, year)

The load is idempotent: tables are dropped and recreated with explicit DDL
on every run, rows are inserted inside a single transaction, and indexes
on ``year`` and ``region`` support the queries in ``sql/queries.sql``.

Usage::

    python src/loading/load.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

SCHEMA: dict[str, str] = {
    "oil_production": """
        CREATE TABLE oil_production (
            country                 TEXT    NOT NULL,
            iso_code                TEXT    NOT NULL,
            region                  TEXT    NOT NULL CHECK (region IN ('Africa','USA','Other')),
            deepwater_basin         TEXT,
            year                    INTEGER NOT NULL,
            oil_production_twh      REAL,
            oil_production_kbd      REAL,
            oil_production_kbd_bbl  REAL,
            oil_prod_per_capita     REAL,
            prod_mwh_per_capita     REAL,
            oil_consumption_twh     REAL,
            fossil_fuel_consumption REAL,
            population              INTEGER,
            gdp                     REAL,
            yoy_change_twh          REAL,
            yoy_change_pct          REAL,
            cumulative_twh          REAL,
            share_of_region_pct     REAL,
            has_production          INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (iso_code, year)
        )""",
    "oil_rents": """
        CREATE TABLE oil_rents (
            country           TEXT    NOT NULL,
            iso_code          TEXT    NOT NULL,
            region            TEXT    NOT NULL,
            year              INTEGER NOT NULL,
            oil_rents_pct_gdp REAL,
            gdp               REAL,
            population        INTEGER,
            oil_rents_usd     REAL,
            gdp_per_capita    REAL,
            has_rents         INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (iso_code, year)
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_prod_year ON oil_production(year)",
    "CREATE INDEX IF NOT EXISTS idx_prod_region_year ON oil_production(region, year)",
    "CREATE INDEX IF NOT EXISTS idx_rents_year ON oil_rents(year)",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | load | %(message)s",
)
logger = logging.getLogger("load")


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load the YAML configuration (honours CONFIG_PATH)."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve(env_var: str, configured: str) -> Path:
    """Resolve a path from an env override or config value."""
    path = Path(os.getenv(env_var, configured))
    return path if path.is_absolute() else PROJECT_ROOT / path


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all project tables."""
    cur = conn.cursor()
    for table, ddl in SCHEMA.items():
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(ddl)
        logger.info("Created table %s", table)
    conn.commit()


def read_processed(path: Path) -> pd.DataFrame:
    """Read a processed CSV, failing clearly if the transform has not run."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run transformation first.")
    return pd.read_csv(path)


def insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert a DataFrame into an existing table (NaN -> NULL)."""
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table}: processed CSV missing columns {missing}")
    frame = df[cols].astype(object).where(df[cols].notna(), None)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, frame.itertuples(index=False, name=None))
    return len(frame)


def load(config_path: str | None = None) -> dict[str, int]:
    """Create the schema and load both processed tables; return row counts."""
    config = load_config(config_path)
    paths = config["paths"]
    processed = _resolve("DATA_PROCESSED_DIR", paths["processed"])
    db_path = _resolve("DB_PATH", paths["database"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    sources = {
        "oil_production": processed / paths["processed_files"]["production"],
        "oil_rents": processed / paths["processed_files"]["rents"],
    }
    frames = {table: read_processed(path) for table, path in sources.items()}

    counts: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        with conn:  # single transaction for all inserts
            for table, df in frames.items():
                counts[table] = insert_frame(conn, table, df)
                logger.info("Loaded %d rows into %s", counts[table], table)
        for stmt in INDEXES:
            conn.execute(stmt)
        conn.commit()
        for table in SCHEMA:
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            if n != counts[table]:
                raise RuntimeError(f"Row count mismatch for {table}: {n} != {counts[table]}")
    finally:
        conn.close()
    logger.info("Database ready at %s: %s", db_path, counts)
    return counts


def main() -> int:
    """CLI entry point."""
    try:
        load()
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit 1
        logger.exception("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
