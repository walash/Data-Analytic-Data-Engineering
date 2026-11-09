"""Loading stage for the African Asylum Seekers & Immigration Flows to the USA pipeline.

Creates a SQLite database (``data/africa_usa_immigration.db`` by default) with:

* ``migration_flows``   - yearly net migration / migrant stock per country (Africa + USA)
* ``remittance_flows``  - yearly remittances (% of GDP) and dependency category (Africa)
* ``country_profiles``  - one row per country summarising migration and remittances

Tables are dropped and recreated on every run so the load is idempotent.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA: Dict[str, str] = {
    "migration_flows": """
        CREATE TABLE migration_flows (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            iso3                      TEXT    NOT NULL,
            country_name              TEXT    NOT NULL,
            year                      INTEGER NOT NULL,
            region                    TEXT    NOT NULL,
            subregion                 TEXT,
            net_migration             REAL,
            net_migration_yoy_change  REAL,
            migration_direction       TEXT,
            migrant_stock_total       REAL,
            migrant_stock_filled      REAL,
            migrant_stock_imputed     INTEGER,
            migrant_stock_pct_pop     REAL,
            migration_intensity       REAL,
            UNIQUE (iso3, year)
        )""",
    "remittance_flows": """
        CREATE TABLE remittance_flows (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            iso3                            TEXT    NOT NULL,
            country_name                    TEXT    NOT NULL,
            year                            INTEGER NOT NULL,
            subregion                       TEXT,
            remittances_pct_gdp             REAL,
            remittance_dependency_category  TEXT,
            migrant_stock_total             REAL,
            migrant_stock_filled            REAL,
            migrant_stock_pct_pop           REAL,
            UNIQUE (iso3, year)
        )""",
    "country_profiles": """
        CREATE TABLE country_profiles (
            iso3                          TEXT PRIMARY KEY,
            country_name                  TEXT NOT NULL,
            region                        TEXT NOT NULL,
            subregion                     TEXT,
            first_year                    INTEGER,
            last_year                     INTEGER,
            total_net_migration           REAL,
            avg_net_migration             REAL,
            latest_net_migration          REAL,
            latest_migrant_stock          REAL,
            latest_migrant_stock_pct_pop  REAL,
            avg_remittances_pct_gdp       REAL,
            latest_remittance_category    TEXT
        )""",
}

INDEXES = [
    "CREATE INDEX idx_migration_year ON migration_flows (year)",
    "CREATE INDEX idx_migration_subregion ON migration_flows (subregion)",
    "CREATE INDEX idx_remittance_year ON remittance_flows (year)",
]


def load_config(path: Path | None = None) -> dict:
    """Load the YAML configuration, honouring the ``CONFIG_PATH`` env var."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(config: dict) -> Tuple[Path, Path]:
    """Return ``(processed_dir, db_path)`` honouring env-var overrides."""
    processed = Path(os.getenv("PROCESSED_DATA_DIR") or config["paths"]["processed_data_dir"])
    db_path = Path(os.getenv("DB_PATH") or config["paths"]["db_path"])
    processed = processed if processed.is_absolute() else PROJECT_ROOT / processed
    db_path = db_path if db_path.is_absolute() else PROJECT_ROOT / db_path
    return processed, db_path


def create_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all tables and indexes."""
    cur = conn.cursor()
    for table, ddl in SCHEMA.items():
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(ddl)
    for stmt in INDEXES:
        cur.execute(stmt)
    conn.commit()
    logger.info("Schema created: %s", ", ".join(SCHEMA))


def insert_frame(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert a DataFrame into an existing table, converting NaN to NULL."""
    clean = df.astype(object).where(pd.notna(df), None)
    cols = list(clean.columns)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    conn.executemany(sql, clean.itertuples(index=False, name=None))
    conn.commit()
    logger.info("Loaded %d rows into %s", len(clean), table)
    return len(clean)


def build_country_profiles(migration: pd.DataFrame, remittances: pd.DataFrame) -> pd.DataFrame:
    """Aggregate one profile row per country from the processed tables."""
    migration = migration.sort_values(["iso3", "year"])
    latest = migration.groupby("iso3").tail(1).set_index("iso3")
    stock = (migration.dropna(subset=["migrant_stock_total"])
                      .groupby("iso3").tail(1).set_index("iso3"))
    agg = migration.groupby("iso3").agg(
        country_name=("country_name", "first"),
        region=("region", "first"),
        subregion=("subregion", "first"),
        first_year=("year", "min"),
        last_year=("year", "max"),
        total_net_migration=("net_migration", "sum"),
        avg_net_migration=("net_migration", "mean"),
    )
    agg["latest_net_migration"] = latest["net_migration"]
    agg["latest_migrant_stock"] = stock["migrant_stock_total"]
    agg["latest_migrant_stock_pct_pop"] = stock["migrant_stock_pct_pop"]

    remit_valid = remittances.dropna(subset=["remittances_pct_gdp"]).sort_values(["iso3", "year"])
    agg["avg_remittances_pct_gdp"] = remit_valid.groupby("iso3")["remittances_pct_gdp"].mean()
    agg["latest_remittance_category"] = (
        remit_valid.groupby("iso3").tail(1).set_index("iso3")["remittance_dependency_category"]
    )
    return agg.reset_index()


def load() -> Path:
    """Run the loading stage and return the database path."""
    config = load_config()
    processed_dir, db_path = get_paths(config)
    paths = config["paths"]
    migration = pd.read_csv(processed_dir / paths["migration_processed_file"])
    remittances = pd.read_csv(processed_dir / paths["remittances_processed_file"])
    migration["migrant_stock_imputed"] = migration["migrant_stock_imputed"].astype(bool).astype(int)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        insert_frame(conn, "migration_flows", migration)
        insert_frame(conn, "remittance_flows", remittances)
        insert_frame(conn, "country_profiles", build_country_profiles(migration, remittances))
    logger.info("Database ready at %s", db_path)
    return db_path


def main() -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        load()
    except (FileNotFoundError, sqlite3.Error, KeyError) as exc:
        logger.critical("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
