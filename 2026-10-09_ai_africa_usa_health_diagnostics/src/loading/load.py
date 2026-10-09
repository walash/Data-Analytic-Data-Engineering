"""Loading stage for the AI Healthcare Diagnostics (Africa & USA) pipeline.

Reads ``data/processed/health_diagnostics_processed.csv`` and loads it into
the SQLite database ``data/health_diagnostics.db`` with three tables:

    health_indicators  one row per country and year (main fact table)
    country_summary    per-country averages, latest values and trends
    yearly_trends      per-year Africa vs USA averages

Tables are created with ``CREATE TABLE IF NOT EXISTS`` and refreshed on each
run (rows deleted then re-inserted inside a single transaction), so the
load is idempotent.

Usage:
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
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

INDICATORS = [
    "health_expenditure_pc", "physicians_per_1000", "under5_mortality",
    "hospital_beds_per_1000", "life_expectancy",
]

SCHEMA = {
    "health_indicators": """
        CREATE TABLE IF NOT EXISTS health_indicators (
            country_code            TEXT    NOT NULL,
            country_name            TEXT    NOT NULL,
            year                    INTEGER NOT NULL,
            health_expenditure_pc   REAL,
            physicians_per_1000     REAL,
            under5_mortality        REAL,
            hospital_beds_per_1000  REAL,
            life_expectancy         REAL,
            africa_flag             INTEGER NOT NULL CHECK (africa_flag IN (0, 1)),
            healthcare_access_score REAL,
            PRIMARY KEY (country_code, year)
        )""",
    "country_summary": """
        CREATE TABLE IF NOT EXISTS country_summary (
            country_code                TEXT PRIMARY KEY,
            country_name                TEXT NOT NULL,
            africa_flag                 INTEGER NOT NULL,
            first_year                  INTEGER,
            latest_year                 INTEGER,
            avg_health_expenditure_pc   REAL,
            avg_physicians_per_1000     REAL,
            avg_under5_mortality        REAL,
            avg_hospital_beds_per_1000  REAL,
            avg_life_expectancy         REAL,
            avg_healthcare_access_score REAL,
            latest_life_expectancy      REAL,
            latest_under5_mortality     REAL,
            life_expectancy_change      REAL,
            under5_mortality_change     REAL
        )""",
    "yearly_trends": """
        CREATE TABLE IF NOT EXISTS yearly_trends (
            year                         INTEGER NOT NULL,
            region                       TEXT    NOT NULL,
            country_count                INTEGER,
            avg_health_expenditure_pc    REAL,
            avg_physicians_per_1000      REAL,
            avg_under5_mortality         REAL,
            avg_hospital_beds_per_1000   REAL,
            avg_life_expectancy          REAL,
            avg_healthcare_access_score  REAL,
            PRIMARY KEY (year, region)
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_hi_year ON health_indicators (year)",
    "CREATE INDEX IF NOT EXISTS idx_hi_africa ON health_indicators (africa_flag, year)",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | load | %(message)s",
)
logger = logging.getLogger(__name__)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load YAML configuration; return an empty dict if it is missing."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def resolve_paths(config: dict[str, Any]) -> tuple[Path, Path]:
    """Return (processed CSV path, SQLite DB path), honouring env overrides."""
    paths = config.get("paths", {})
    csv_path = Path(os.getenv(
        "PROCESSED_FILE",
        PROJECT_ROOT / paths.get("processed_file", "data/processed/health_diagnostics_processed.csv"),
    ))
    db_path = Path(os.getenv("DB_PATH", PROJECT_ROOT / paths.get("db", "data/health_diagnostics.db")))
    return csv_path, db_path


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    for name, ddl in SCHEMA.items():
        conn.execute(ddl)
        logger.debug("Ensured table %s", name)
    for ddl in INDEXES:
        conn.execute(ddl)


def build_country_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the main table to one row per country."""
    rows = []
    for code, grp in df.sort_values("year").groupby("country_code"):
        le = grp.dropna(subset=["life_expectancy"])
        u5 = grp.dropna(subset=["under5_mortality"])
        rows.append({
            "country_code": code,
            "country_name": grp["country_name"].iloc[-1],
            "africa_flag": int(grp["africa_flag"].iloc[0]),
            "first_year": int(grp["year"].min()),
            "latest_year": int(grp["year"].max()),
            **{f"avg_{c}": grp[c].mean() for c in INDICATORS},
            "avg_healthcare_access_score": grp["healthcare_access_score"].mean(),
            "latest_life_expectancy": le["life_expectancy"].iloc[-1] if len(le) else None,
            "latest_under5_mortality": u5["under5_mortality"].iloc[-1] if len(u5) else None,
            "life_expectancy_change": (le["life_expectancy"].iloc[-1] - le["life_expectancy"].iloc[0])
            if len(le) > 1 else None,
            "under5_mortality_change": (u5["under5_mortality"].iloc[-1] - u5["under5_mortality"].iloc[0])
            if len(u5) > 1 else None,
        })
    return pd.DataFrame(rows).round(3)


def build_yearly_trends(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the main table to one row per year and region."""
    work = df.assign(region=df["africa_flag"].map({1: "Africa", 0: "USA"}))
    agg = work.groupby(["year", "region"]).agg(
        country_count=("country_code", "nunique"),
        **{f"avg_{c}": (c, "mean") for c in INDICATORS},
        avg_healthcare_access_score=("healthcare_access_score", "mean"),
    )
    return agg.reset_index().round(3)


def replace_table(conn: sqlite3.Connection, table: str, frame: pd.DataFrame) -> int:
    """Delete existing rows and insert ``frame`` into ``table``."""
    conn.execute(f"DELETE FROM {table}")
    frame = frame.astype(object).where(frame.notna(), None)
    cols = ", ".join(frame.columns)
    placeholders = ", ".join("?" for _ in frame.columns)
    conn.executemany(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders})",
        frame.itertuples(index=False, name=None),
    )
    return len(frame)


def load() -> dict[str, int]:
    """Run the full load and return row counts per table."""
    csv_path, db_path = resolve_paths(load_config())
    if not csv_path.exists():
        raise FileNotFoundError(f"Processed file not found: {csv_path}. Run transform.py first.")

    df = pd.read_csv(csv_path)
    missing = {"country_code", "country_name", "year", "africa_flag", *INDICATORS} - set(df.columns)
    if missing:
        raise ValueError(f"Processed file is missing columns: {sorted(missing)}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        counts["health_indicators"] = replace_table(conn, "health_indicators", df[
            ["country_code", "country_name", "year", *INDICATORS, "africa_flag", "healthcare_access_score"]
        ])
        counts["country_summary"] = replace_table(conn, "country_summary", build_country_summary(df))
        counts["yearly_trends"] = replace_table(conn, "yearly_trends", build_yearly_trends(df))
        conn.commit()

    for table, n in counts.items():
        logger.info("Loaded %-18s %4d rows", table, n)
    logger.info("Database ready at %s", db_path)
    return counts


def main() -> int:
    """CLI entry point."""
    try:
        load()
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        logger.critical("Load failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
