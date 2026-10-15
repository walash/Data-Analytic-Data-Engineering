"""Loading stage: load processed brain-drain CSVs into SQLite (data/brain_drain.db).

Tables
------
dim_country            country dimension (PK country_code)
fact_indicator         long fact table: country x indicator x year
country_year_metrics   wide country-year panel with derived metrics
brain_drain_index      latest snapshot + Brain Drain Pressure Index
pipeline_runs          audit log of each load (timestamp + row counts)

Usage:
    python src/loading/load.py [--config path] [--processed-dir dir] [--db path]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA = {
    "dim_country": """
        CREATE TABLE dim_country (
            country_code TEXT PRIMARY KEY,
            country_name TEXT NOT NULL,
            iso3         TEXT,
            region       TEXT NOT NULL
        )""",
    "fact_indicator": """
        CREATE TABLE fact_indicator (
            country_code    TEXT    NOT NULL REFERENCES dim_country(country_code),
            indicator       TEXT    NOT NULL,
            indicator_code  TEXT    NOT NULL,
            indicator_label TEXT,
            year            INTEGER NOT NULL,
            value           REAL    NOT NULL,
            PRIMARY KEY (country_code, indicator, year)
        )""",
    "country_year_metrics": """
        CREATE TABLE country_year_metrics (
            country_code               TEXT    NOT NULL REFERENCES dim_country(country_code),
            country_name               TEXT,
            region                     TEXT,
            year                       INTEGER NOT NULL,
            net_migration              REAL,
            remittances_usd            REAL,
            remittances_pct_gdp        REAL,
            gdp_per_capita             REAL,
            unemployment               REAL,
            tertiary_enrollment        REAL,
            migrant_stock              REAL,
            population                 REAL,
            population_filled          REAL,
            net_migration_per_1000     REAL,
            remittances_per_capita_usd REAL,
            remittances_usd_bn         REAL,
            migrant_stock_pct_pop      REAL,
            gdp_per_capita_growth_pct  REAL,
            is_net_emigration          INTEGER,
            PRIMARY KEY (country_code, year)
        )""",
    "brain_drain_index": """
        CREATE TABLE brain_drain_index (
            country_code                TEXT PRIMARY KEY REFERENCES dim_country(country_code),
            country_name                TEXT,
            region                      TEXT,
            net_migration               REAL,
            net_migration_year          INTEGER,
            net_migration_per_1000      REAL,
            net_migration_per_1000_year INTEGER,
            remittances_usd             REAL,
            remittances_usd_year        INTEGER,
            remittances_pct_gdp         REAL,
            remittances_pct_gdp_year    INTEGER,
            gdp_per_capita              REAL,
            gdp_per_capita_year         INTEGER,
            unemployment                REAL,
            unemployment_year           INTEGER,
            tertiary_enrollment         REAL,
            tertiary_enrollment_year    INTEGER,
            migrant_stock               REAL,
            migrant_stock_year          INTEGER,
            population                  REAL,
            population_year             INTEGER,
            net_migration_cumulative    REAL,
            score_unemployment          REAL,
            score_gdp_per_capita_gap    REAL,
            score_net_outflow_rate      REAL,
            score_remittance_dependence REAL,
            brain_drain_index           REAL,
            brain_drain_rank            INTEGER
        )""",
    "pipeline_runs": """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at   TEXT NOT NULL,
            row_counts  TEXT NOT NULL
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_fact_indicator_year ON fact_indicator(indicator, year)",
    "CREATE INDEX IF NOT EXISTS idx_cym_year ON country_year_metrics(year)",
    "CREATE INDEX IF NOT EXISTS idx_cym_region ON country_year_metrics(region, year)",
    "CREATE INDEX IF NOT EXISTS idx_country_region ON dim_country(region)",
]

# Load order respects foreign keys (dimension first).
LOAD_ORDER = ["dim_country", "fact_indicator", "country_year_metrics", "brain_drain_index"]
CSV_FOR_TABLE = {
    "dim_country": "countries.csv",
    "fact_indicator": "indicators_long.csv",
    "country_year_metrics": "country_year_metrics.csv",
    "brain_drain_index": "brain_drain_index.csv",
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def create_schema(conn: sqlite3.Connection) -> None:
    """(Re)create analytical tables; pipeline_runs is preserved across runs."""
    cur = conn.cursor()
    for table in reversed(LOAD_ORDER):
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    for ddl in SCHEMA.values():
        cur.execute(ddl)
    conn.commit()


def read_processed(path: Path, table: str, conn: sqlite3.Connection) -> pd.DataFrame:
    """Read a processed CSV and keep only columns defined in the target table."""
    if not path.exists():
        raise FileNotFoundError(f"Processed file not found: {path}. Run transform.py first.")
    df = pd.read_csv(path)
    table_cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    missing = [c for c in table_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name} is missing columns required by {table}: {missing}")
    return df[table_cols]


def insert_dataframe(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    cols = list(df.columns)
    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    records = [
        tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in row)
        for row in df.itertuples(index=False, name=None)
    ]
    conn.executemany(sql, records)
    return len(records)


def load(config_path: str | Path | None = None, processed_dir: str | Path | None = None,
         db_path: str | Path | None = None) -> dict[str, int]:
    config = load_config(config_path)
    processed = _resolve(processed_dir or config["paths"]["processed_dir"])
    database = _resolve(db_path or os.environ.get("DB_PATH") or config["paths"]["database"])
    database.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        for table in LOAD_ORDER:
            df = read_processed(processed / CSV_FOR_TABLE[table], table, conn)
            counts[table] = insert_dataframe(conn, table, df)
            logger.info("Loaded %-22s %5d rows", table, counts[table])
        for stmt in INDEXES:
            conn.execute(stmt)
        conn.execute("INSERT INTO pipeline_runs (loaded_at, row_counts) VALUES (?, ?)",
                     (datetime.now(timezone.utc).isoformat(), json.dumps(counts)))
        conn.commit()
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"Foreign key violations after load: {fk_errors[:5]}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    logger.info("Database ready at %s", database)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load processed data into SQLite")
    parser.add_argument("--config", default=None)
    parser.add_argument("--processed-dir", default=None)
    parser.add_argument("--db", default=None, help="SQLite path (default data/brain_drain.db)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    try:
        load(args.config, args.processed_dir, args.db)
    except Exception as exc:  # noqa: BLE001
        logger.error("Load failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
