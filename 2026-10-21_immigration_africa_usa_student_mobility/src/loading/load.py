"""Loading stage: load processed CSVs into the SQLite analytics database.

Creates (or fully rebuilds) ``data/student_mobility.db`` with tables:
    countries               - country dimension (15 African focus countries + USA)
    indicators              - indicator dimension
    indicator_observations  - long fact table (iso3, indicator_key, year, value)
    country_year_metrics    - wide fact table with derived mobility metrics
    mobility_snapshot       - latest-value snapshot per African country
    pipeline_runs           - audit log of load executions

Usage:
    python src/loading/load.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA: dict[str, str] = {
    "countries": """
        CREATE TABLE countries (
            iso3          TEXT PRIMARY KEY,
            country_name  TEXT NOT NULL,
            region        TEXT NOT NULL,
            sub_region    TEXT NOT NULL,
            is_africa     INTEGER NOT NULL CHECK (is_africa IN (0, 1)),
            is_usa        INTEGER NOT NULL CHECK (is_usa IN (0, 1)),
            wb_name       TEXT
        )""",
    "indicators": """
        CREATE TABLE indicators (
            indicator_key   TEXT PRIMARY KEY,
            indicator_code  TEXT NOT NULL UNIQUE,
            indicator_name  TEXT NOT NULL,
            unit            TEXT NOT NULL,
            source          TEXT NOT NULL
        )""",
    "indicator_observations": """
        CREATE TABLE indicator_observations (
            iso3            TEXT    NOT NULL REFERENCES countries(iso3),
            indicator_key   TEXT    NOT NULL REFERENCES indicators(indicator_key),
            indicator_code  TEXT    NOT NULL,
            year            INTEGER NOT NULL,
            value           REAL    NOT NULL,
            is_imputed      INTEGER NOT NULL DEFAULT 0,
            obs_status      TEXT,
            PRIMARY KEY (iso3, indicator_key, year)
        )""",
    "country_year_metrics": """
        CREATE TABLE country_year_metrics (
            iso3                            TEXT    NOT NULL REFERENCES countries(iso3),
            year                            INTEGER NOT NULL,
            country_name                    TEXT,
            region                          TEXT,
            sub_region                      TEXT,
            is_africa                       INTEGER,
            net_migration                   REAL,
            tertiary_enrollment             REAL,
            education_expenditure           REAL,
            remittances                     REAL,
            gdp_per_capita                  REAL,
            population                      REAL,
            net_migration_rate_per_1000     REAL,
            remittances_per_capita_usd      REAL,
            remittances_pct_gdp             REAL,
            education_spend_per_capita_usd  REAL,
            gdp_pc_pct_of_usa               REAL,
            enrollment_gap_vs_usa_pp        REAL,
            edu_spend_pc_pct_of_usa         REAL,
            gdp_per_capita_yoy_pct          REAL,
            remittances_yoy_pct             REAL,
            tertiary_enrollment_yoy_pct     REAL,
            population_yoy_pct              REAL,
            enrollment_reference_year       INTEGER,
            mobility_pressure_index         REAL,
            income_group_proxy              TEXT,
            PRIMARY KEY (iso3, year)
        )""",
    "mobility_snapshot": """
        CREATE TABLE mobility_snapshot (
            iso3                              TEXT PRIMARY KEY REFERENCES countries(iso3),
            country_name                      TEXT,
            sub_region                        TEXT,
            tertiary_enrollment               REAL,
            tertiary_enrollment_year          INTEGER,
            education_expenditure             REAL,
            education_expenditure_year        INTEGER,
            gdp_per_capita                    REAL,
            gdp_per_capita_year               INTEGER,
            remittances                       REAL,
            remittances_year                  INTEGER,
            remittances_pct_gdp               REAL,
            remittances_pct_gdp_year          INTEGER,
            net_migration_rate_per_1000       REAL,
            net_migration_rate_per_1000_year  INTEGER,
            population                        REAL,
            population_year                   INTEGER,
            gdp_pc_pct_of_usa                 REAL,
            gdp_pc_pct_of_usa_year            INTEGER,
            enrollment_gap_vs_usa_pp          REAL,
            enrollment_gap_vs_usa_pp_year     INTEGER,
            mobility_pressure_index           REAL,
            mobility_pressure_index_year      INTEGER,
            pressure_rank                     INTEGER
        )""",
    "pipeline_runs": """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            loaded_at_utc TEXT NOT NULL,
            table_name    TEXT NOT NULL,
            row_count     INTEGER NOT NULL
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_obs_indicator_year "
    "ON indicator_observations (indicator_key, year)",
    "CREATE INDEX IF NOT EXISTS ix_cym_year ON country_year_metrics (year)",
    "CREATE INDEX IF NOT EXISTS ix_cym_africa_year ON country_year_metrics (is_africa, year)",
    "CREATE INDEX IF NOT EXISTS ix_countries_subregion ON countries (sub_region)",
]

# Load order respects foreign keys (dimensions first).
TABLE_FILES = [
    ("countries", "countries.csv"),
    ("indicators", "indicators.csv"),
    ("indicator_observations", "indicators_long.csv"),
    ("country_year_metrics", "country_year_metrics.csv"),
    ("mobility_snapshot", "mobility_snapshot.csv"),
]


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(cfg: dict[str, Any]) -> tuple[Path, Path]:
    processed = PROJECT_ROOT / cfg["paths"]["processed_dir"]
    db_path = Path(os.environ.get("DB_PATH", PROJECT_ROOT / cfg["paths"]["database"]))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return processed, db_path


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate analytical tables (pipeline_runs is preserved)."""
    cur = conn.cursor()
    for table in reversed([t for t, _ in TABLE_FILES]):
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    for ddl in SCHEMA.values():
        cur.execute(ddl)
    conn.commit()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def load_table(conn: sqlite3.Connection, table: str, csv_path: Path) -> int:
    """Insert a processed CSV into ``table`` aligning columns to the schema."""
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found; run transformation first")
    df = pd.read_csv(csv_path)
    cols = _table_columns(conn, table)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        logger.warning("%s: columns missing in CSV, loading as NULL: %s", table, missing)
        for col in missing:
            df[col] = np.nan
    df = df[cols]
    df = df.astype(object).where(pd.notna(df), None)

    placeholders = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, df.itertuples(index=False, name=None))
    conn.commit()
    logger.info("Loaded %-24s %6d rows", table, len(df))
    return len(df)


def validate(conn: sqlite3.Connection) -> None:
    """Basic integrity checks after load."""
    orphans = conn.execute(
        "SELECT COUNT(*) FROM indicator_observations o "
        "LEFT JOIN countries c ON c.iso3 = o.iso3 WHERE c.iso3 IS NULL"
    ).fetchone()[0]
    if orphans:
        raise ValueError(f"{orphans} observations reference unknown countries")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise ValueError(f"Foreign key violations: {fk_errors[:5]}")
    n_africa = conn.execute("SELECT COUNT(*) FROM countries WHERE is_africa = 1").fetchone()[0]
    logger.info("Validation passed (%d African countries, 0 orphans)", n_africa)


def load(config_path: str | None = None) -> dict[str, int]:
    cfg = load_config(config_path)
    processed, db_path = get_paths(cfg)
    logger.info("Loading into %s", db_path)

    counts: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        for table, filename in TABLE_FILES:
            counts[table] = load_table(conn, table, processed / filename)
        for ddl in INDEXES:
            conn.execute(ddl)
        loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.executemany(
            "INSERT INTO pipeline_runs (loaded_at_utc, table_name, row_count) VALUES (?, ?, ?)",
            [(loaded_at, t, n) for t, n in counts.items()],
        )
        conn.commit()
        validate(conn)
        conn.execute("ANALYZE")
    finally:
        conn.close()
    logger.info("Load complete: %s", counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load processed CSVs into SQLite")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    try:
        load(args.config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Load failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
