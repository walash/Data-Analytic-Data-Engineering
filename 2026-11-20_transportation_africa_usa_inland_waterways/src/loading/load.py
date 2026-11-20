"""Loading stage of the Inland Waterways (Africa & USA) data pipeline.

Creates (or refreshes) the SQLite database ``data/waterways.db`` and loads
the processed CSVs written by ``src/transformation/transform.py`` into five
tables:

* waterway_infrastructure - one row per country (network length, density)
* us_freight              - USACE Mississippi/Ohio River tonnage by commodity
* africa_freight          - African river freight & passenger traffic
* container_traffic       - World Bank container port TEU by country-year
* country_metrics         - combined country-year fact table

Every table is defined with an explicit schema (CREATE TABLE IF NOT EXISTS)
and reloaded idempotently: rows are deleted then re-inserted inside one
transaction per table, so repeated runs never duplicate data.
"""

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is in requirements.txt
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("load")

SCHEMA = {
    "waterway_infrastructure": """
        CREATE TABLE IF NOT EXISTS waterway_infrastructure (
            iso3               TEXT PRIMARY KEY,
            iso2               TEXT,
            country            TEXT NOT NULL,
            region             TEXT NOT NULL,
            total_waterway_km  REAL,
            navigable_km       REAL,
            navigable_share    REAL,
            land_area_sq_km    REAL,
            waterway_density   REAL,
            major_rivers       TEXT,
            year               INTEGER
        )""",
    "us_freight": """
        CREATE TABLE IF NOT EXISTS us_freight (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            year                   INTEGER NOT NULL,
            waterway               TEXT NOT NULL,
            commodity              TEXT NOT NULL,
            direction              TEXT,
            tonnage_million        REAL,
            freight_thousand_tons  REAL,
            country                TEXT,
            iso3                   TEXT,
            region                 TEXT,
            decade                 TEXT,
            UNIQUE (year, waterway, commodity, direction)
        )""",
    "africa_freight": """
        CREATE TABLE IF NOT EXISTS africa_freight (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            country                TEXT NOT NULL,
            iso3                   TEXT NOT NULL,
            waterway               TEXT NOT NULL,
            year                   INTEGER NOT NULL,
            freight_thousand_tons  REAL,
            passengers_thousand    REAL,
            ports_count            REAL,
            region                 TEXT,
            decade                 TEXT,
            navigable_km           REAL,
            waterway_density       REAL,
            population             REAL,
            utilization_ratio      REAL,
            freight_per_1000_pop   REAL,
            passengers_per_port    REAL,
            UNIQUE (iso3, waterway, year)
        )""",
    "container_traffic": """
        CREATE TABLE IF NOT EXISTS container_traffic (
            iso3                      TEXT NOT NULL,
            country_code              TEXT,
            country_name              TEXT,
            year                      INTEGER NOT NULL,
            container_teu             REAL,
            liner_connectivity_index  REAL,
            region                    TEXT,
            decade                    TEXT,
            PRIMARY KEY (iso3, year)
        )""",
    "country_metrics": """
        CREATE TABLE IF NOT EXISTS country_metrics (
            iso3                            TEXT NOT NULL,
            iso2                            TEXT,
            country_name                    TEXT,
            region                          TEXT,
            year                            INTEGER NOT NULL,
            decade                          TEXT,
            gdp_usd                         REAL,
            population                      REAL,
            gdp_per_capita                  REAL,
            container_teu                   REAL,
            teu_per_1000_pop                REAL,
            liner_connectivity_index        REAL,
            waterway_freight_thousand_tons  REAL,
            freight_per_1000_pop            REAL,
            total_waterway_km               REAL,
            navigable_km                    REAL,
            waterway_density                REAL,
            utilization_ratio               REAL,
            PRIMARY KEY (iso3, year)
        )""",
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_us_freight_year ON us_freight (year)",
    "CREATE INDEX IF NOT EXISTS idx_us_freight_commodity ON us_freight (commodity)",
    "CREATE INDEX IF NOT EXISTS idx_africa_freight_country_year ON africa_freight (iso3, year)",
    "CREATE INDEX IF NOT EXISTS idx_container_year ON container_traffic (year)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_region_year ON country_metrics (region, year)",
]

# Processed CSV file feeding each table.
TABLE_SOURCES = {
    "waterway_infrastructure": "waterway_infrastructure.csv",
    "us_freight": "us_freight.csv",
    "africa_freight": "africa_freight.csv",
    "container_traffic": "container_traffic.csv",
    "country_metrics": "country_metrics.csv",
}


def setup_logging(level_name):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path=CONFIG_PATH):
    defaults = {"processed_dir": "data/processed", "db_path": "data/waterways.db"}
    if yaml is None:
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
        defaults.update(cfg.get("data", {}))
    except FileNotFoundError:
        logger.warning("Config %s not found; using default paths", path)
    except yaml.YAMLError as exc:
        logger.error("Invalid YAML in %s (%s); using default paths", path, exc)
    return defaults


def resolve_paths(cfg):
    """Return (processed_dir, db_path) honouring DATA_DIR and DB_PATH env vars."""
    data_dir = os.getenv("DATA_DIR")
    processed = Path(data_dir).resolve() / "processed" if data_dir else PROJECT_ROOT / cfg["processed_dir"]
    db_env = os.getenv("DB_PATH")
    db_path = Path(db_env) if db_env else PROJECT_ROOT / cfg["db_path"]
    return processed.resolve(), db_path.resolve()


def create_schema(conn):
    """Create all tables and indexes if they do not yet exist."""
    cur = conn.cursor()
    for table, ddl in SCHEMA.items():
        cur.execute(ddl)
        logger.debug("Ensured table %s", table)
    for ddl in INDEXES:
        cur.execute(ddl)
    conn.commit()
    logger.info("Schema ready: %s", ", ".join(SCHEMA))


def table_columns(conn, table):
    """Return the insertable columns of ``table`` (excludes autoincrement id)."""
    rows = conn.execute("PRAGMA table_info({})".format(table)).fetchall()
    return [r[1] for r in rows if not (r[1] == "id" and r[5] == 1)]


def load_table(conn, table, csv_path):
    """Replace the contents of ``table`` with the rows of ``csv_path``."""
    df = pd.read_csv(csv_path)
    columns = [c for c in table_columns(conn, table) if c in df.columns]
    missing = set(table_columns(conn, table)) - set(columns)
    if missing:
        logger.warning("%s: CSV lacks columns %s (stored as NULL)", table, sorted(missing))
    df = df[columns].astype(object).where(pd.notna(df[columns]), None)
    records = [tuple(row) for row in df.itertuples(index=False, name=None)]
    sql = "INSERT INTO {} ({}) VALUES ({})".format(
        table, ", ".join(columns), ", ".join("?" for _ in columns)
    )
    with conn:  # one transaction per table: all-or-nothing reload
        conn.execute("DELETE FROM {}".format(table))
        conn.executemany(sql, records)
    count = conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
    logger.info("Loaded %-24s %4d rows", table, count)
    return count


def load(processed_dir=None, db_path=None):
    """Run the loading stage; returns dict table -> row count."""
    cfg = load_config()
    default_processed, default_db = resolve_paths(cfg)
    processed_dir = Path(processed_dir) if processed_dir else default_processed
    db_path = Path(db_path) if db_path else default_db
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Loading %s -> %s", processed_dir, db_path)

    summary = {}
    conn = sqlite3.connect(str(db_path))
    try:
        create_schema(conn)
        for table, filename in TABLE_SOURCES.items():
            csv_path = processed_dir / filename
            if not csv_path.exists():
                raise FileNotFoundError(
                    "Processed file {} missing - run the transformation stage first".format(csv_path)
                )
            summary[table] = load_table(conn, table, csv_path)
    except sqlite3.Error as exc:
        logger.error("SQLite error while loading: %s", exc)
        raise
    finally:
        conn.close()
    logger.info("Loading complete: %s", summary)
    return summary


def main():
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    try:
        load()
    except Exception as exc:
        logger.exception("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
