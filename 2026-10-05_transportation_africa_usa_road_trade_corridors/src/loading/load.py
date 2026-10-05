"""Loading stage for the Africa-USA Road Infrastructure & Trade Corridors pipeline.

Creates the SQLite database ``data/transport_trade.db`` and loads:

    raw_indicators          - long format, one row per country/indicator/observed year
                              (cleaned raw observations, no forward-fill)
    master_transport_trade  - wide country-year table from data/processed/
    country_summary         - latest values per country from data/processed/
    lpi_rankings            - LPI survey-year scores with overall and Africa-only ranks

Indexes are created on the common filter/join columns.
"""

import logging
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from transformation.transform import load_raw  # noqa: E402

RAW_DIR = Path(os.getenv("RAW_DATA_DIR", PROJECT_ROOT / "data" / "raw"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DATA_DIR", PROJECT_ROOT / "data" / "processed"))
DB_PATH = Path(os.getenv("DB_PATH", PROJECT_ROOT / "data" / "transport_trade.db"))

SCHEMA = {
    "raw_indicators": """
        CREATE TABLE raw_indicators (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            country_code  TEXT    NOT NULL,
            country_name  TEXT    NOT NULL,
            region        TEXT    NOT NULL,
            indicator     TEXT    NOT NULL,
            year          INTEGER NOT NULL,
            value         REAL    NOT NULL,
            UNIQUE (country_code, indicator, year)
        )""",
    "master_transport_trade": """
        CREATE TABLE master_transport_trade (
            country_code            TEXT    NOT NULL,
            country_name            TEXT,
            region                  TEXT,
            year                    INTEGER NOT NULL,
            lpi_overall             REAL,
            lpi_infrastructure      REAL,
            road_density            REAL,
            paved_roads_pct         REAL,
            merchandise_exports     REAL,
            trade_pct_gdp           REAL,
            gdp_per_capita          REAL,
            lpi_observed            INTEGER,
            lpi_gap_vs_usa          REAL,
            lpi_infra_gap_vs_usa    REAL,
            lpi_pct_of_usa          REAL,
            trade_openness_category TEXT,
            PRIMARY KEY (country_code, year)
        )""",
    "country_summary": """
        CREATE TABLE country_summary (
            country_code             TEXT PRIMARY KEY,
            country_name             TEXT,
            region                   TEXT,
            lpi_overall              REAL,
            lpi_overall_year         INTEGER,
            lpi_infrastructure       REAL,
            lpi_infrastructure_year  INTEGER,
            road_density             REAL,
            road_density_year        INTEGER,
            paved_roads_pct          REAL,
            paved_roads_pct_year     INTEGER,
            merchandise_exports      REAL,
            merchandise_exports_year INTEGER,
            trade_pct_gdp            REAL,
            trade_pct_gdp_year       INTEGER,
            gdp_per_capita           REAL,
            gdp_per_capita_year      INTEGER,
            lpi_gap_vs_usa           REAL,
            lpi_infra_gap_vs_usa     REAL,
            trade_openness_category  TEXT,
            lpi_rank                 INTEGER,
            first_year               INTEGER,
            last_year                INTEGER
        )""",
    "lpi_rankings": """
        CREATE TABLE lpi_rankings (
            country_code        TEXT    NOT NULL,
            country_name        TEXT,
            region              TEXT,
            year                INTEGER NOT NULL,
            lpi_overall         REAL    NOT NULL,
            lpi_infrastructure  REAL,
            overall_rank        INTEGER,
            africa_rank         INTEGER,
            PRIMARY KEY (country_code, year)
        )""",
}

INDEXES = [
    "CREATE INDEX idx_raw_indicator_year ON raw_indicators (indicator, year)",
    "CREATE INDEX idx_raw_country ON raw_indicators (country_code)",
    "CREATE INDEX idx_master_year ON master_transport_trade (year)",
    "CREATE INDEX idx_master_region ON master_transport_trade (region)",
    "CREATE INDEX idx_summary_region ON country_summary (region)",
    "CREATE INDEX idx_lpi_year_rank ON lpi_rankings (year, overall_rank)",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("load")


def create_schema(conn):
    """Drop and recreate all tables so each run is idempotent.

    Args:
        conn: Open ``sqlite3.Connection``.
    """
    cur = conn.cursor()
    for table, ddl in SCHEMA.items():
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(ddl)
        logger.info("Created table %s", table)
    conn.commit()


def read_processed(name, processed_dir=PROCESSED_DIR):
    """Read a processed CSV, raising a clear error if it is missing.

    Args:
        name: File name inside ``data/processed``.
        processed_dir: Processed data directory.

    Returns:
        pandas DataFrame.

    Raises:
        FileNotFoundError: When the file does not exist (run transform first).
    """
    path = Path(processed_dir) / name
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run src/transformation/transform.py first")
    return pd.read_csv(path)


def insert_frame(conn, table, df):
    """Append a DataFrame to an existing table, keeping only schema columns.

    Args:
        conn: Open ``sqlite3.Connection``.
        table: Destination table name.
        df: Data to insert.

    Returns:
        Number of rows inserted.
    """
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[1] != "id"]
    df = df[[c for c in cols if c in df.columns]]
    df = df.astype(object).where(pd.notna(df), None)
    df.to_sql(table, conn, if_exists="append", index=False)
    logger.info("Loaded %d rows into %s", len(df), table)
    return len(df)


def build_lpi_rankings(raw):
    """Derive LPI rankings from observed survey-year values (no forward-fill).

    Built from the cleaned raw observations so no LPI survey is lost to the
    sparse-row filter applied to the master table.

    Args:
        raw: Long-format cleaned observations (output of ``load_raw``) with region.

    Returns:
        DataFrame with overall_rank (all 16 countries) and africa_rank per year.
    """
    subset = raw[raw["indicator"].isin(["lpi_overall", "lpi_infrastructure"])]
    lpi = subset.pivot_table(index=["country_code", "country_name", "region", "year"],
                             columns="indicator", values="value", aggfunc="first").reset_index()
    lpi.columns.name = None
    lpi = lpi[lpi["lpi_overall"].notna()].copy()
    lpi["overall_rank"] = lpi.groupby("year")["lpi_overall"].rank(ascending=False, method="min")
    africa = lpi["region"] == "Africa"
    lpi.loc[africa, "africa_rank"] = (lpi[africa].groupby("year")["lpi_overall"]
                                      .rank(ascending=False, method="min"))
    lpi["overall_rank"] = lpi["overall_rank"].astype("Int64")
    lpi["africa_rank"] = lpi["africa_rank"].astype("Int64")
    return lpi[["country_code", "country_name", "region", "year", "lpi_overall",
                "lpi_infrastructure", "overall_rank", "africa_rank"]]


def load(db_path=DB_PATH, raw_dir=RAW_DIR, processed_dir=PROCESSED_DIR):
    """Create the database and load every table.

    Args:
        db_path: SQLite file path.
        raw_dir: Raw CSV directory (for raw_indicators).
        processed_dir: Processed CSV directory.

    Returns:
        dict mapping table name -> row count loaded.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    master = read_processed("master_transport_trade.csv", processed_dir)
    summary = read_processed("country_summary.csv", processed_dir)
    raw = load_raw(raw_dir)
    raw["region"] = raw["country_code"].map(lambda c: "North America" if c == "USA" else "Africa")
    counts = {}
    with sqlite3.connect(db_path) as conn:
        create_schema(conn)
        counts["raw_indicators"] = insert_frame(conn, "raw_indicators", raw)
        counts["master_transport_trade"] = insert_frame(conn, "master_transport_trade", master)
        counts["country_summary"] = insert_frame(conn, "country_summary", summary)
        counts["lpi_rankings"] = insert_frame(conn, "lpi_rankings", build_lpi_rankings(raw))
        for ddl in INDEXES:
            conn.execute(ddl)
        conn.commit()
        logger.info("Created %d indexes", len(INDEXES))
    logger.info("Database ready at %s: %s", db_path, counts)
    return counts


def main():
    """CLI entry point. Returns a process exit code."""
    try:
        load()
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        logger.error("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
