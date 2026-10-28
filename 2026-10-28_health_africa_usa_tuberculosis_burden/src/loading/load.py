"""
Loading module for the Africa & USA Tuberculosis Burden pipeline.

Creates a star-like SQLite schema at ``data/tb_burden.db`` and loads the
processed CSVs produced by the transformation step:

* ``dim_country``           - one row per country (region, sub-region)
* ``fact_tb_incidence``     - WHO incidence per 100k with uncertainty bounds
* ``fact_tb_mortality``     - World Bank TB death rate per 100k
* ``fact_tb_treatment``     - World Bank treatment success rate (%)
* ``fact_health_expenditure`` - World Bank health spending (% of GDP)
* ``tb_burden_summary``     - merged, enriched country-year analytical table
* ``v_latest_country_burden`` - view with each country's most recent metrics

The load is idempotent: tables are dropped and recreated in a single
transaction, so re-running the pipeline never duplicates rows.

Usage::

    python src/loading/load.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("tb_load")

SCHEMA: Dict[str, str] = {
    "dim_country": """
        CREATE TABLE dim_country (
            country_code TEXT PRIMARY KEY,
            country_name TEXT NOT NULL,
            region       TEXT NOT NULL CHECK (region IN ('Africa', 'USA')),
            sub_region   TEXT NOT NULL
        )""",
    "fact_tb_incidence": """
        CREATE TABLE fact_tb_incidence (
            country_code             TEXT    NOT NULL REFERENCES dim_country(country_code),
            year                     INTEGER NOT NULL,
            tb_incidence_per_100k    REAL    NOT NULL CHECK (tb_incidence_per_100k >= 0),
            low_estimate             REAL,
            high_estimate            REAL,
            uncertainty_range        REAL,
            burden_category          TEXT,
            incidence_yoy_change_pct REAL,
            PRIMARY KEY (country_code, year)
        )""",
    "fact_tb_mortality": """
        CREATE TABLE fact_tb_mortality (
            country_code          TEXT    NOT NULL REFERENCES dim_country(country_code),
            year                  INTEGER NOT NULL,
            tb_mortality_per_100k REAL    NOT NULL CHECK (tb_mortality_per_100k >= 0),
            PRIMARY KEY (country_code, year)
        )""",
    "fact_tb_treatment": """
        CREATE TABLE fact_tb_treatment (
            country_code             TEXT    NOT NULL REFERENCES dim_country(country_code),
            year                     INTEGER NOT NULL,
            tb_treatment_success_pct REAL    NOT NULL
                CHECK (tb_treatment_success_pct BETWEEN 0 AND 100),
            PRIMARY KEY (country_code, year)
        )""",
    "fact_health_expenditure": """
        CREATE TABLE fact_health_expenditure (
            country_code               TEXT    NOT NULL REFERENCES dim_country(country_code),
            year                       INTEGER NOT NULL,
            health_expenditure_pct_gdp REAL    NOT NULL,
            PRIMARY KEY (country_code, year)
        )""",
    "tb_burden_summary": """
        CREATE TABLE tb_burden_summary (
            country_code                TEXT    NOT NULL REFERENCES dim_country(country_code),
            country_name                TEXT    NOT NULL,
            region                      TEXT    NOT NULL,
            sub_region                  TEXT    NOT NULL,
            year                        INTEGER NOT NULL,
            tb_incidence_per_100k       REAL,
            low_estimate                REAL,
            high_estimate               REAL,
            uncertainty_range           REAL,
            burden_category             TEXT,
            incidence_yoy_change_pct    REAL,
            tb_mortality_per_100k       REAL,
            case_fatality_ratio_pct     REAL,
            tb_treatment_success_pct    REAL,
            treatment_gap_to_target_pct REAL,
            meets_treatment_target      INTEGER,
            health_expenditure_pct_gdp  REAL,
            data_completeness           INTEGER NOT NULL,
            PRIMARY KEY (country_code, year)
        )""",
}

INDEXES: List[str] = [
    "CREATE INDEX idx_incidence_year ON fact_tb_incidence(year)",
    "CREATE INDEX idx_mortality_year ON fact_tb_mortality(year)",
    "CREATE INDEX idx_treatment_year ON fact_tb_treatment(year)",
    "CREATE INDEX idx_summary_region_year ON tb_burden_summary(region, year)",
    "CREATE INDEX idx_summary_subregion ON tb_burden_summary(sub_region)",
]

VIEWS: Dict[str, str] = {
    "v_latest_country_burden": """
        CREATE VIEW v_latest_country_burden AS
        SELECT s.*
        FROM tb_burden_summary s
        JOIN (SELECT country_code, MAX(year) AS max_year
              FROM tb_burden_summary
              WHERE tb_incidence_per_100k IS NOT NULL
              GROUP BY country_code) m
          ON s.country_code = m.country_code AND s.year = m.max_year""",
}

# processed_csv key -> (table name, columns to load)
TABLE_SOURCES: Dict[str, tuple] = {
    "countries": ("dim_country", ["country_code", "country_name", "region", "sub_region"]),
    "incidence": ("fact_tb_incidence", ["country_code", "year", "tb_incidence_per_100k",
                                        "low_estimate", "high_estimate", "uncertainty_range",
                                        "burden_category", "incidence_yoy_change_pct"]),
    "mortality": ("fact_tb_mortality", ["country_code", "year", "tb_mortality_per_100k"]),
    "treatment_success": ("fact_tb_treatment",
                          ["country_code", "year", "tb_treatment_success_pct"]),
    "health_expenditure": ("fact_health_expenditure",
                           ["country_code", "year", "health_expenditure_pct_gdp"]),
    "merged": ("tb_burden_summary", None),
}


def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load YAML configuration (``CONFIG_PATH`` env var overrides default)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve processed directory and database path (env ``DB_PATH`` overrides)."""
    db = Path(os.environ.get("DB_PATH", config["paths"]["database"]))
    if not db.is_absolute():
        db = PROJECT_ROOT / db
    db.parent.mkdir(parents=True, exist_ok=True)
    return {"processed": PROJECT_ROOT / config["paths"]["processed_dir"], "db": db}


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all views, tables and indexes."""
    cur = conn.cursor()
    for view in VIEWS:
        cur.execute(f"DROP VIEW IF EXISTS {view}")
    # Drop facts before the dimension so FK references never dangle.
    for table in reversed(list(SCHEMA)):
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    for table, ddl in SCHEMA.items():
        cur.execute(ddl)
        logger.debug("Created table %s", table)
    logger.info("Schema created: %d tables", len(SCHEMA))


def create_indexes_and_views(conn: sqlite3.Connection) -> None:
    """Create analytical indexes and views after the bulk insert."""
    for stmt in INDEXES:
        conn.execute(stmt)
    for ddl in VIEWS.values():
        conn.execute(ddl)
    logger.info("Created %d indexes and %d view(s)", len(INDEXES), len(VIEWS))


def read_processed(path: Path, columns: List[str] | None) -> pd.DataFrame:
    """Read a processed CSV, optionally restricting to ``columns``."""
    if not path.exists():
        raise FileNotFoundError(f"Processed file missing: {path} (run transform first)")
    df = pd.read_csv(path)
    return df[columns] if columns else df


def insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert a DataFrame into an existing table using parameterised SQL."""
    df = df.astype(object).where(pd.notna(df), None)
    cols = ", ".join(df.columns)
    param_marks = ", ".join("?" for _ in df.columns)
    conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({param_marks})",
                     df.itertuples(index=False, name=None))
    logger.info("Loaded %-24s %6d rows", table, len(df))
    return len(df)


def validate(conn: sqlite3.Connection) -> None:
    """Run post-load integrity checks and raise on failure."""
    orphans = conn.execute(
        """SELECT COUNT(*) FROM tb_burden_summary s
           LEFT JOIN dim_country c USING (country_code)
           WHERE c.country_code IS NULL""").fetchone()[0]
    if orphans:
        raise ValueError(f"{orphans} summary rows reference unknown countries")
    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk_errors:
        raise ValueError(f"Foreign key violations: {fk_errors[:5]}")
    usa = conn.execute(
        "SELECT COUNT(*) FROM tb_burden_summary WHERE region = 'USA'").fetchone()[0]
    logger.info("Validation passed (USA rows: %d)", usa)


def load(config: Dict[str, Any]) -> Dict[str, int]:
    """Create schema and load all processed files; returns rows per table."""
    paths = get_paths(config)
    names = config["paths"]["processed_csv"]
    counts: Dict[str, int] = {}
    conn = sqlite3.connect(paths["db"])
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:  # single transaction: commit on success, rollback on error
            create_schema(conn)
            for key, (table, columns) in TABLE_SOURCES.items():
                df = read_processed(paths["processed"] / names[key], columns)
                counts[table] = insert_frame(conn, table, df)
            create_indexes_and_views(conn)
            validate(conn)
        conn.execute("ANALYZE")
    finally:
        conn.close()
    logger.info("Load complete -> %s: %s", paths["db"], counts)
    return counts


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Load processed TB data into SQLite")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        load(config)
        return 0
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        logger.error("Load failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
