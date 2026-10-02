"""Loading stage of the Africa LNG Exports to the USA pipeline.

Creates (or updates) the SQLite database ``data/lng_pipeline.db`` and loads:

* ``lng_trade_raw``          - row-level Comtrade LNG trade (all flows and
  partners) from ``data/processed/lng_trade_clean.csv``.
* ``wb_indicators``          - World Bank indicators in long format from
  ``data/raw/worldbank_energy_indicators.csv``.
* ``wb_indicators_wide``     - one row per country-year with one column per
  indicator, from ``data/processed/wb_indicators_wide.csv``.
* ``lng_exports_processed``  - exporter-year analytical table from
  ``data/processed/lng_exports_processed.csv``.

Every table has a natural primary key and rows are written with
``INSERT ... ON CONFLICT DO UPDATE`` (upsert), so re-running the pipeline is
idempotent. A plain-text run report can be generated with ``--report``.

Usage::

    python src/loading/load.py            # create schema + upsert all tables
    python src/loading/load.py --report   # also write data/pipeline_report.txt
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("load")

SCHEMA: Dict[str, str] = {
    "lng_trade_raw": """
        CREATE TABLE IF NOT EXISTS lng_trade_raw (
            year             INTEGER NOT NULL,
            reporter_code    INTEGER NOT NULL,
            country_code     TEXT,
            reporter         TEXT    NOT NULL,
            partner_code     INTEGER NOT NULL,
            partner          TEXT    NOT NULL,
            flow             TEXT    NOT NULL CHECK (flow IN ('X','M','RX')),
            trade_value_usd  REAL    NOT NULL CHECK (trade_value_usd >= 0),
            qty_kg           REAL,
            commodity        TEXT,
            loaded_at        TEXT    NOT NULL,
            PRIMARY KEY (year, reporter_code, partner_code, flow)
        );""",
    "wb_indicators": """
        CREATE TABLE IF NOT EXISTS wb_indicators (
            country_code     TEXT    NOT NULL,
            country          TEXT    NOT NULL,
            year             INTEGER NOT NULL,
            indicator        TEXT    NOT NULL,
            indicator_name   TEXT    NOT NULL,
            value            REAL    NOT NULL,
            loaded_at        TEXT    NOT NULL,
            PRIMARY KEY (country_code, year, indicator)
        );""",
    "wb_indicators_wide": """
        CREATE TABLE IF NOT EXISTS wb_indicators_wide (
            country_code                  TEXT    NOT NULL,
            country                       TEXT    NOT NULL,
            year                          INTEGER NOT NULL,
            gas_rents_pct_gdp             REAL,
            oil_rents_pct_gdp             REAL,
            total_resource_rents_pct_gdp  REAL,
            merchandise_exports_usd       REAL,
            electricity_from_gas_pct      REAL,
            gdp_per_capita_usd            REAL,
            population                    REAL,
            loaded_at                     TEXT    NOT NULL,
            PRIMARY KEY (country_code, year)
        );""",
    "lng_exports_processed": """
        CREATE TABLE IF NOT EXISTS lng_exports_processed (
            country_code                    TEXT    NOT NULL,
            reporter_code                   INTEGER NOT NULL,
            country                         TEXT    NOT NULL,
            year                            INTEGER NOT NULL,
            total_export_value_usd          REAL    NOT NULL,
            export_value_billion_usd        REAL    NOT NULL,
            total_export_qty_kg             REAL,
            usa_export_value_usd            REAL    NOT NULL DEFAULT 0,
            usa_export_value_million_usd    REAL    NOT NULL DEFAULT 0,
            usa_share_pct                   REAL,
            export_share_of_africa_pct      REAL,
            num_destinations                INTEGER,
            top_destination                 TEXT,
            top_destination_value_usd       REAL,
            prev_year_available             INTEGER,
            yoy_growth_pct                  REAL,
            gas_rents_pct_gdp               REAL,
            oil_rents_pct_gdp               REAL,
            total_resource_rents_pct_gdp    REAL,
            merchandise_exports_usd         REAL,
            electricity_from_gas_pct        REAL,
            gdp_per_capita_usd              REAL,
            population                      REAL,
            lng_share_of_merch_exports_pct  REAL,
            gas_dependency_score            REAL,
            loaded_at                       TEXT    NOT NULL,
            PRIMARY KEY (country_code, year)
        );""",
}

PRIMARY_KEYS: Dict[str, Sequence[str]] = {
    "lng_trade_raw": ("year", "reporter_code", "partner_code", "flow"),
    "wb_indicators": ("country_code", "year", "indicator"),
    "wb_indicators_wide": ("country_code", "year"),
    "lng_exports_processed": ("country_code", "year"),
}

INDEXES: List[str] = [
    "CREATE INDEX IF NOT EXISTS idx_trade_partner "
    "ON lng_trade_raw (partner_code, flow, year);",
    "CREATE INDEX IF NOT EXISTS idx_trade_reporter "
    "ON lng_trade_raw (reporter, flow, year);",
    "CREATE INDEX IF NOT EXISTS idx_wb_indicator "
    "ON wb_indicators (indicator_name, year);",
    "CREATE INDEX IF NOT EXISTS idx_exports_year "
    "ON lng_exports_processed (year);",
]


def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var wins)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve input CSVs and the database path (``DB_PATH`` env var wins)."""
    raw = PROJECT_ROOT / config["paths"]["raw_data_dir"]
    processed = PROJECT_ROOT / config["paths"]["processed_data_dir"]
    db_path = Path(os.getenv("DB_PATH") or config["paths"]["db_path"])
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    report = PROJECT_ROOT / config["paths"]["report_path"]
    return {
        "lng_trade_raw": processed / "lng_trade_clean.csv",
        "wb_indicators": raw / config["paths"]["worldbank_file"],
        "wb_indicators_wide": processed / "wb_indicators_wide.csv",
        "lng_exports_processed": processed / "lng_exports_processed.csv",
        "db": db_path,
        "report": report,
    }


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with sensible pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not yet exist."""
    with conn:
        for name, ddl in SCHEMA.items():
            conn.execute(ddl)
            logger.debug("Ensured table %s", name)
        for ddl in INDEXES:
            conn.execute(ddl)
    logger.info("Schema ready: %s", ", ".join(SCHEMA))


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Return the column names of ``table`` in definition order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table});")]


def _clean_value(value: Any) -> Any:
    """Convert pandas/numpy scalars and NaN to SQLite-friendly Python values."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _batches(rows: List[tuple], size: int) -> Iterable[List[tuple]]:
    """Yield ``rows`` in chunks of ``size``."""
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def upsert_dataframe(conn: sqlite3.Connection, table: str, df: pd.DataFrame,
                     batch_size: int = 500) -> int:
    """Upsert ``df`` into ``table`` using its primary key; return row count."""
    loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    columns = [c for c in table_columns(conn, table)
               if c in df.columns or c == "loaded_at"]
    frame = df.copy()
    frame["loaded_at"] = loaded_at
    frame = frame[columns]
    keys = PRIMARY_KEYS[table]
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates};"
    )
    rows = [tuple(_clean_value(v) for v in rec)
            for rec in frame.itertuples(index=False, name=None)]
    with conn:
        for chunk in _batches(rows, batch_size):
            conn.executemany(sql, chunk)
    logger.info("Upserted %d rows into %s", len(rows), table)
    return len(rows)


def read_source(path: Path, table: str, config: Dict[str, Any]) -> pd.DataFrame:
    """Read and lightly validate the CSV feeding ``table``."""
    if not path.exists():
        raise FileNotFoundError(f"Missing input for {table}: {path}")
    df = pd.read_csv(path)
    if table == "wb_indicators":
        names = {c["iso3"]: c["name"] for c in config["countries"]}
        df = df.dropna(subset=["country_code", "year", "indicator", "value"])
        df = df[df["country_code"].isin(names)].copy()
        df["country"] = df["country_code"].map(names)
        df["indicator_name"] = df["indicator"].map(config["indicators"]) \
            .fillna(df["indicator_name"])
        df["year"] = df["year"].astype(int)
    keys = list(PRIMARY_KEYS[table])
    dupes = df.duplicated(subset=keys).sum()
    if dupes:
        logger.warning("%s: dropping %d duplicate key rows", table, dupes)
        df = df.drop_duplicates(subset=keys, keep="last")
    return df


def load(config: Dict[str, Any] | None = None) -> Dict[str, int]:
    """Create the schema and upsert every table; return row counts."""
    config = config or load_config()
    paths = get_paths(config)
    batch = int(config["pipeline"].get("upsert_batch_size", 500))
    counts: Dict[str, int] = {}
    conn = connect(paths["db"])
    try:
        create_schema(conn)
        for table in SCHEMA:
            df = read_source(paths[table], table, config)
            upsert_dataframe(conn, table, df, batch)
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM {table};"
            ).fetchone()[0]
    finally:
        conn.close()
    logger.info("Database %s loaded: %s", paths["db"], counts)
    return counts


def generate_report(config: Dict[str, Any] | None = None) -> Path:
    """Write a plain-text summary of the loaded database and return its path."""
    config = config or load_config()
    paths = get_paths(config)
    if not paths["db"].exists():
        raise FileNotFoundError(f"Database not found: {paths['db']}")
    conn = sqlite3.connect(paths["db"])
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t};").fetchone()[0]
                  for t in SCHEMA}
        top = conn.execute(
            """SELECT country, ROUND(SUM(export_value_billion_usd), 2),
                      ROUND(SUM(usa_export_value_million_usd), 2)
               FROM lng_exports_processed GROUP BY country
               ORDER BY 2 DESC LIMIT 5;"""
        ).fetchall()
        years = conn.execute(
            """SELECT year, ROUND(SUM(export_value_billion_usd), 2),
                      ROUND(SUM(usa_export_value_million_usd), 2)
               FROM lng_exports_processed GROUP BY year ORDER BY year;"""
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "Africa LNG Exports to the USA - pipeline report",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"Database:  {paths['db'].name}",
        "",
        "Table row counts:",
        *[f"  {t:<24}{n:>8,}" for t, n in counts.items()],
        "",
        "Top exporters (sum of available years):",
        "  country          LNG exports (USD bn)   to USA (USD m)",
        *[f"  {c:<16}{v:>20,.2f}{u:>17,.2f}" for c, v, u in top],
        "",
        "African LNG exports by year:",
        *[f"  {y}: USD {v:,.2f} bn total, USD {u:,.2f} m to USA"
          for y, v, u in years],
    ]
    paths["report"].parent.mkdir(parents=True, exist_ok=True)
    paths["report"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", paths["report"])
    return paths["report"]


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true",
                        help="write data/pipeline_report.txt after loading")
    args = parser.parse_args(argv)
    try:
        load()
        if args.report:
            generate_report()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the CLI
        logger.exception("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
