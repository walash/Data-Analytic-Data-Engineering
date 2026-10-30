"""Loading stage: load processed CSVs into the SQLite warehouse.

Creates ``data/natural_gas.db`` with four tables:

    countries                 country dimension
    gas_indicators            long fact table (country, year, indicator, value)
    country_year_panel        wide panel used by dashboards / notebook
    pipeline_infrastructure   reference catalogue of major gas pipelines

Tables are fully refreshed on every run inside a single transaction so the
database is never left half-loaded.
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
    "countries": """
        CREATE TABLE countries (
            iso3          TEXT PRIMARY KEY,
            country_code  TEXT,
            country_name  TEXT NOT NULL,
            region        TEXT NOT NULL,
            is_focus      INTEGER NOT NULL DEFAULT 0 CHECK (is_focus IN (0, 1))
        )""",
    "gas_indicators": """
        CREATE TABLE gas_indicators (
            iso3            TEXT NOT NULL REFERENCES countries(iso3),
            year            INTEGER NOT NULL,
            indicator_key   TEXT NOT NULL,
            indicator_code  TEXT NOT NULL,
            value           REAL NOT NULL,
            PRIMARY KEY (iso3, year, indicator_key)
        )""",
    "country_year_panel": """
        CREATE TABLE country_year_panel (
            iso3                            TEXT NOT NULL REFERENCES countries(iso3),
            country_name                    TEXT,
            region                          TEXT,
            year                            INTEGER NOT NULL,
            ngas_electricity_pct            REAL,
            ngas_rents_gdp_pct              REAL,
            fuel_exports_pct                REAL,
            ngas_electricity_pct_yoy_change REAL,
            ngas_rents_gdp_pct_yoy_change   REAL,
            fuel_exports_pct_yoy_change     REAL,
            PRIMARY KEY (iso3, year)
        )""",
    "pipeline_infrastructure": """
        CREATE TABLE pipeline_infrastructure (
            pipeline_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            origin_iso3     TEXT NOT NULL,
            destination     TEXT,
            region          TEXT,
            length_km       REAL,
            capacity_bcm_y  REAL,
            start_year      INTEGER,
            status          TEXT
        )""",
}

INDEXES: List[str] = [
    "CREATE INDEX IF NOT EXISTS idx_gi_indicator_year ON gas_indicators (indicator_key, year)",
    "CREATE INDEX IF NOT EXISTS idx_panel_region_year ON country_year_panel (region, year)",
    "CREATE INDEX IF NOT EXISTS idx_pipe_origin ON pipeline_infrastructure (origin_iso3)",
]

LOAD_ORDER = ["countries", "gas_indicators", "country_year_panel", "pipeline_infrastructure"]
SOURCE_FILES = {
    "countries": "countries.csv",
    "gas_indicators": "gas_indicators_long.csv",
    "country_year_panel": "country_year_panel.csv",
    "pipeline_infrastructure": "pipeline_infrastructure.csv",
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    db_path = PROJECT_ROOT / config["paths"]["database"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return {"processed": PROJECT_ROOT / config["paths"]["processed_dir"], "database": db_path}


def create_schema(conn: sqlite3.Connection) -> None:
    for table in reversed(LOAD_ORDER):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for table in LOAD_ORDER:
        conn.execute(SCHEMA[table])


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def read_processed(processed_dir: Path, filename: str) -> pd.DataFrame:
    path = processed_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"Processed file not found: {path}. Run transform.py first.")
    return pd.read_csv(path)


def insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert the columns of ``df`` that exist in the target table."""
    cols = [c for c in table_columns(conn, table) if c in df.columns]
    if not cols:
        raise ValueError(f"No matching columns between CSV and table {table}")
    subset = df[cols].astype(object).where(pd.notna(df[cols]), None)
    bind_params = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({bind_params})"
    conn.executemany(sql, subset.itertuples(index=False, name=None))
    return len(subset)


def load(config: Dict[str, Any]) -> Dict[str, int]:
    paths = get_paths(config)
    counts: Dict[str, int] = {}
    conn = sqlite3.connect(paths["database"])
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            create_schema(conn)
            for table in LOAD_ORDER:
                df = read_processed(paths["processed"], SOURCE_FILES[table])
                counts[table] = insert_frame(conn, table, df)
                logger.info("Loaded %d rows into %s", counts[table], table)
            for stmt in INDEXES:
                conn.execute(stmt)
        for table in LOAD_ORDER:
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            if n != counts[table]:
                raise RuntimeError(f"Row count mismatch in {table}: {n} != {counts[table]}")
    finally:
        conn.close()
    logger.info("Database ready at %s", paths["database"])
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Load processed data into SQLite")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Load summary: %s", load(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
