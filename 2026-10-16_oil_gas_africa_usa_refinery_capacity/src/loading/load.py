"""
Loading stage - Oil & Gas Refinery Capacity & Fuel Access (Africa vs USA).

Creates a star-style SQLite schema at ``data/oil_gas_refinery.db`` and loads
the processed outputs of ``transform.py``.

Schema
------
* ``dim_country``            - one row per country (ISO codes, region, OPEC)
* ``dim_indicator``          - World Bank indicator metadata
* ``fact_energy_indicators`` - country x year panel with derived metrics
* ``country_latest_snapshot``- latest available value per indicator/country
* ``data_quality_report``    - coverage statistics from the transform stage
* ``pipeline_runs``          - audit log of every load
* ``v_regional_yearly``      - view: Africa vs USA yearly aggregates

The load is idempotent: tables are truncated and reloaded inside a single
transaction so readers never observe a half-loaded database.

Usage
-----
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
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS dim_country (
        iso3          TEXT PRIMARY KEY,
        iso2          TEXT NOT NULL UNIQUE,
        country       TEXT NOT NULL,
        region        TEXT NOT NULL,
        subregion     TEXT NOT NULL,
        opec_member   INTEGER NOT NULL CHECK (opec_member IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dim_indicator (
        indicator_key   TEXT PRIMARY KEY,
        wb_code         TEXT NOT NULL UNIQUE,
        value_column    TEXT NOT NULL UNIQUE,
        description     TEXT NOT NULL,
        source_file     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fact_energy_indicators (
        iso3                                  TEXT    NOT NULL REFERENCES dim_country(iso3),
        year                                  INTEGER NOT NULL CHECK (year BETWEEN 1960 AND 2100),
        oil_rents_pct_gdp                     REAL,
        fuel_exports_pct_merchandise          REAL,
        fuel_imports_pct_merchandise          REAL,
        electricity_access_pct                REAL,
        fossil_fuel_pct_energy                REAL,
        gdp_per_capita_usd                    REAL,
        oil_rents_pct_gdp_imputed             INTEGER NOT NULL DEFAULT 0,
        fuel_exports_pct_merchandise_imputed  INTEGER NOT NULL DEFAULT 0,
        fuel_imports_pct_merchandise_imputed  INTEGER NOT NULL DEFAULT 0,
        electricity_access_pct_imputed        INTEGER NOT NULL DEFAULT 0,
        fossil_fuel_pct_energy_imputed        INTEGER NOT NULL DEFAULT 0,
        gdp_per_capita_usd_imputed            INTEGER NOT NULL DEFAULT 0,
        net_fuel_trade_pp                     REAL,
        fuel_import_export_ratio              REAL,
        electricity_access_gap_pct            REAL,
        refining_gap_flag                     INTEGER NOT NULL DEFAULT 0,
        energy_poverty_flag                   INTEGER NOT NULL DEFAULT 0,
        fuel_trade_archetype                  TEXT,
        gdp_pc_pct_of_usa                     REAL,
        oil_rents_yoy_pp                      REAL,
        electricity_access_yoy_pp             REAL,
        PRIMARY KEY (iso3, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS country_latest_snapshot (
        iso3                               TEXT PRIMARY KEY REFERENCES dim_country(iso3),
        oil_rents_pct_gdp                  REAL,
        oil_rents_pct_gdp_year             INTEGER,
        fuel_exports_pct_merchandise       REAL,
        fuel_exports_pct_merchandise_year  INTEGER,
        fuel_imports_pct_merchandise       REAL,
        fuel_imports_pct_merchandise_year  INTEGER,
        electricity_access_pct             REAL,
        electricity_access_pct_year        INTEGER,
        fossil_fuel_pct_energy             REAL,
        fossil_fuel_pct_energy_year        INTEGER,
        gdp_per_capita_usd                 REAL,
        gdp_per_capita_usd_year            INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_quality_report (
        indicator          TEXT PRIMARY KEY,
        rows               INTEGER,
        non_null           INTEGER,
        null_pct           REAL,
        imputed            INTEGER,
        countries_covered  INTEGER,
        min_year           INTEGER,
        max_year           INTEGER,
        min                REAL,
        max                REAL,
        mean               REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        loaded_at_utc TEXT    NOT NULL,
        table_name    TEXT    NOT NULL,
        row_count     INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fact_year ON fact_energy_indicators(year)",
    "CREATE INDEX IF NOT EXISTS idx_fact_archetype ON fact_energy_indicators(fuel_trade_archetype)",
    "DROP VIEW IF EXISTS v_regional_yearly",
    """
    CREATE VIEW v_regional_yearly AS
    SELECT  c.region,
            f.year,
            COUNT(*)                                     AS countries,
            ROUND(AVG(f.oil_rents_pct_gdp), 3)           AS avg_oil_rents_pct_gdp,
            ROUND(AVG(f.fuel_exports_pct_merchandise), 3) AS avg_fuel_exports_pct,
            ROUND(AVG(f.fuel_imports_pct_merchandise), 3) AS avg_fuel_imports_pct,
            ROUND(AVG(f.electricity_access_pct), 3)      AS avg_electricity_access_pct,
            ROUND(AVG(f.gdp_per_capita_usd), 2)          AS avg_gdp_per_capita_usd,
            SUM(f.refining_gap_flag)                     AS refining_gap_countries
    FROM    fact_energy_indicators f
    JOIN    dim_country c USING (iso3)
    GROUP BY c.region, f.year
    """,
]

FACT_COLUMNS = [
    "iso3", "year", "oil_rents_pct_gdp", "fuel_exports_pct_merchandise",
    "fuel_imports_pct_merchandise", "electricity_access_pct", "fossil_fuel_pct_energy",
    "gdp_per_capita_usd", "oil_rents_pct_gdp_imputed", "fuel_exports_pct_merchandise_imputed",
    "fuel_imports_pct_merchandise_imputed", "electricity_access_pct_imputed",
    "fossil_fuel_pct_energy_imputed", "gdp_per_capita_usd_imputed", "net_fuel_trade_pp",
    "fuel_import_export_ratio", "electricity_access_gap_pct", "refining_gap_flag",
    "energy_poverty_flag", "fuel_trade_archetype", "gdp_pc_pct_of_usa", "oil_rents_yoy_pp",
    "electricity_access_yoy_pp",
]


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent, timestamped format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration (``$CONFIG_PATH`` or ``config/config.yaml``)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_processed(path: Path) -> pd.DataFrame:
    """Read a processed CSV, failing loudly if the transform has not run.

    Raises:
        FileNotFoundError: If the file is missing.
        ValueError: If the file is empty.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run transform.py first")
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"{path} is empty")
    return df


def to_sql_records(df: pd.DataFrame, columns: List[str]) -> List[tuple]:
    """Convert a DataFrame to SQLite-friendly tuples (NaN -> None, bool -> int)."""
    out = df[columns].copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].astype(int)
    out = out.astype(object).where(pd.notna(out), None)
    return [tuple(r) for r in out.itertuples(index=False, name=None)]


def insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, columns: List[str]) -> int:
    """Truncate ``table`` and bulk insert ``df[columns]``. Returns row count."""
    conn.execute(f"DELETE FROM {table}")
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    records = to_sql_records(df, columns)
    conn.executemany(sql, records)
    logger.info("Loaded %4d rows -> %s", len(records), table)
    return len(records)


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables, indexes and views (idempotent)."""
    for statement in DDL:
        conn.execute(statement)


def build_dimensions(config: Dict[str, Any]) -> Dict[str, pd.DataFrame]:
    """Build dimension DataFrames from the configuration and master data."""
    master = read_processed(PROJECT_ROOT / config["paths"]["master_dataset"])
    names = master.drop_duplicates("iso3").set_index("iso3")["country"]
    countries = pd.DataFrame.from_dict(config["regions"], orient="index")
    countries.index.name = "iso3"
    countries = countries.reset_index()
    countries["country"] = countries["iso3"].map(names).fillna(countries["iso3"])
    countries["opec_member"] = countries["opec_member"].astype(int)

    indicators = pd.DataFrame([
        {"indicator_key": key, "wb_code": spec["code"], "value_column": spec["value_column"],
         "description": spec["description"], "source_file": spec["file"]}
        for key, spec in config["worldbank"]["indicators"].items()
    ])
    return {"countries": countries, "indicators": indicators, "master": master}


def load(config: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
    """Run the loading stage.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        Mapping of table name -> rows loaded.
    """
    config = config or load_config()
    paths = config["paths"]
    db_path = PROJECT_ROOT / paths["database"]
    db_path.parent.mkdir(parents=True, exist_ok=True)

    dims = build_dimensions(config)
    snapshot = read_processed(PROJECT_ROOT / paths["latest_snapshot"])
    quality = read_processed(PROJECT_ROOT / paths["data_quality_report"])
    snapshot_cols = [c for c in snapshot.columns if c == "iso3" or c.endswith("_year")
                     or c in FACT_COLUMNS]

    counts: Dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        create_schema(conn)
        with conn:  # single atomic transaction
            for table in ("fact_energy_indicators", "country_latest_snapshot"):
                conn.execute(f"DELETE FROM {table}")
            counts["dim_country"] = insert(
                conn, "dim_country", dims["countries"],
                ["iso3", "iso2", "country", "region", "subregion", "opec_member"])
            counts["dim_indicator"] = insert(
                conn, "dim_indicator", dims["indicators"],
                ["indicator_key", "wb_code", "value_column", "description", "source_file"])
            counts["fact_energy_indicators"] = insert(
                conn, "fact_energy_indicators", dims["master"], FACT_COLUMNS)
            counts["country_latest_snapshot"] = insert(
                conn, "country_latest_snapshot", snapshot, snapshot_cols)
            counts["data_quality_report"] = insert(
                conn, "data_quality_report", quality, list(quality.columns))
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO pipeline_runs (loaded_at_utc, table_name, row_count) VALUES (?, ?, ?)",
                [(now, t, n) for t, n in counts.items()],
            )
        bad_fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad_fk:
            raise RuntimeError(f"Foreign key violations: {bad_fk[:5]}")
    finally:
        conn.close()
    logger.info("Database ready: %s", db_path.relative_to(PROJECT_ROOT))
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Load processed data into SQLite.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        setup_logging(config["pipeline"].get("log_level", "INFO"))
        load(config)
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit code 1
        setup_logging()
        logger.exception("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
