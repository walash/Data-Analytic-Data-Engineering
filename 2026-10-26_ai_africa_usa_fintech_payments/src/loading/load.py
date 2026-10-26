"""Loading stage of the AI-Driven Fintech & Digital Payments (Africa + USA) pipeline.

Creates (or updates) the SQLite database ``data/fintech_africa_usa.db`` with
three tables:

``raw_indicators``
    Long-format copy of every raw World Bank observation
    (country_code, indicator_code, year) -> value.

``fintech_master``
    Country-year table produced by ``src/transformation/transform.py``.

``country_summary``
    One row per country with the latest indicator values and derived metrics.

All inserts are idempotent upserts (``INSERT ... ON CONFLICT DO UPDATE``)
keyed on each table's primary key, so the pipeline can be re-run on every
schedule without duplicating rows. After loading, row counts in the database
are verified against the source files.

Usage::

    python src/loading/load.py
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("load")

SCHEMA: Dict[str, str] = {
    "raw_indicators": """
        CREATE TABLE IF NOT EXISTS raw_indicators (
            country_code    TEXT    NOT NULL,
            country_name    TEXT,
            indicator_key   TEXT    NOT NULL,
            indicator_code  TEXT    NOT NULL,
            year            INTEGER NOT NULL,
            value           REAL,
            loaded_at       TEXT    NOT NULL,
            PRIMARY KEY (country_code, indicator_code, year)
        )""",
    "fintech_master": """
        CREATE TABLE IF NOT EXISTS fintech_master (
            country_code                    TEXT    NOT NULL,
            country_name                    TEXT,
            region                          TEXT,
            sub_region                      TEXT,
            year                            INTEGER NOT NULL,
            financial_inclusion_pct         REAL,
            financial_inclusion_pct_filled  REAL,
            internet_users_pct              REAL,
            mobile_subs_per_100             REAL,
            remittances_usd                 REAL,
            gdp_per_capita_usd              REAL,
            population                      REAL,
            total_gdp_usd                   REAL,
            fintech_readiness_score         REAL,
            remittance_to_gdp_ratio         REAL,
            internet_users_yoy_pp           REAL,
            remittances_yoy_pct             REAL,
            loaded_at                       TEXT    NOT NULL,
            PRIMARY KEY (country_code, year)
        )""",
    "country_summary": """
        CREATE TABLE IF NOT EXISTS country_summary (
            country_code                        TEXT PRIMARY KEY,
            country_name                        TEXT,
            region                              TEXT,
            sub_region                          TEXT,
            financial_inclusion_pct             REAL,
            financial_inclusion_pct_year        INTEGER,
            internet_users_pct                  REAL,
            internet_users_pct_year             INTEGER,
            mobile_subs_per_100                 REAL,
            mobile_subs_per_100_year            INTEGER,
            remittances_usd                     REAL,
            remittances_usd_year                INTEGER,
            gdp_per_capita_usd                  REAL,
            gdp_per_capita_usd_year             INTEGER,
            population                          REAL,
            population_year                     INTEGER,
            fintech_readiness_score             REAL,
            fintech_readiness_score_year        INTEGER,
            remittance_to_gdp_ratio             REAL,
            remittance_to_gdp_ratio_year        INTEGER,
            financial_inclusion_2017            REAL,
            financial_inclusion_change_pp       REAL,
            internet_users_change_since_2020_pp REAL,
            readiness_rank                      INTEGER,
            loaded_at                           TEXT NOT NULL
        )""",
}

INDEXES: List[str] = [
    "CREATE INDEX IF NOT EXISTS idx_raw_indicator_year ON raw_indicators (indicator_code, year)",
    "CREATE INDEX IF NOT EXISTS idx_master_year ON fintech_master (year)",
    "CREATE INDEX IF NOT EXISTS idx_master_region ON fintech_master (region, year)",
]

PRIMARY_KEYS: Dict[str, Sequence[str]] = {
    "raw_indicators": ("country_code", "indicator_code", "year"),
    "fintech_master": ("country_code", "year"),
    "country_summary": ("country_code",),
}


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``config/config.yaml`` (or ``$CONFIG_PATH``) into a dictionary."""
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_path(env_var: str, configured: str) -> Path:
    """Resolve a path from an environment override or the config (relative to project root)."""
    path = Path(os.environ.get(env_var, configured))
    return path if path.is_absolute() else PROJECT_ROOT / path


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not already exist."""
    for name, ddl in SCHEMA.items():
        conn.execute(ddl)
        logger.debug("Ensured table %s", name)
    for ddl in INDEXES:
        conn.execute(ddl)
    conn.commit()


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Return the column names of ``table`` in declaration order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _clean(value: Any) -> Any:
    """Convert pandas/numpy scalars and NaN into SQLite-friendly Python values."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
        if isinstance(value, float) and math.isnan(value):
            return None
    return value


def _batches(rows: List[tuple], size: int) -> Iterable[List[tuple]]:
    """Yield successive ``size``-row slices of ``rows``."""
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def upsert_dataframe(conn: sqlite3.Connection, table: str, df: pd.DataFrame, batch_size: int = 500) -> int:
    """Upsert a DataFrame into ``table`` using ``INSERT ... ON CONFLICT DO UPDATE``.

    Only columns that exist in the table are written; ``loaded_at`` is set to
    the current UTC timestamp.

    Returns:
        Number of rows submitted.
    """
    cols = [c for c in table_columns(conn, table) if c in df.columns or c == "loaded_at"]
    df = df.copy()
    df["loaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    keys = PRIMARY_KEYS[table]
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in keys)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}"
    )
    rows = [tuple(_clean(v) for v in rec) for rec in df[cols].itertuples(index=False, name=None)]
    for batch in _batches(rows, batch_size):
        conn.executemany(sql, batch)
    conn.commit()
    logger.info("Upserted %d rows into %s", len(rows), table)
    return len(rows)


def read_raw_indicators(raw_dir: Path, config: Dict[str, Any]) -> pd.DataFrame:
    """Read every raw indicator CSV into one long-format DataFrame."""
    api = config["world_bank_api"]
    specs = dict(api["indicators"])
    specs.update(api.get("supplementary_indicators") or {})
    frames = []
    for key, spec in specs.items():
        path = raw_dir / spec["file"]
        if not path.exists():
            logger.warning("Raw file not found, skipping: %s", path)
            continue
        df = pd.read_csv(path)
        value_col = "value_usd" if "value_usd" in df.columns else "value"
        df = df.rename(columns={value_col: "value", "indicator": "indicator_code"})
        df["indicator_key"] = key
        df["year"] = pd.to_numeric(df["year"], errors="coerce")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["country_code", "year"])
        df["year"] = df["year"].astype(int)
        frames.append(df[["country_code", "country_name", "indicator_key", "indicator_code", "year", "value"]])
    if not frames:
        raise FileNotFoundError(f"No raw indicator files found in {raw_dir}")
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates(subset=["country_code", "indicator_code", "year"], keep="last")


def read_processed(path: Path) -> pd.DataFrame:
    """Read a processed CSV, raising a clear error if the transform stage has not run."""
    if not path.exists():
        raise FileNotFoundError(f"Processed file missing (run transform.py first): {path}")
    return pd.read_csv(path)


def verify_counts(conn: sqlite3.Connection, expected: Dict[str, int]) -> bool:
    """Compare database row counts with the number of source rows.

    Returns:
        True if every table holds at least the expected number of rows.
    """
    ok = True
    for table, n_expected in expected.items():
        n_actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        status = "OK" if n_actual >= n_expected else "MISMATCH"
        if n_actual < n_expected:
            ok = False
        logger.info("Row count %-16s source=%-5d db=%-5d %s", table, n_expected, n_actual, status)
    return ok


def load(config: Dict[str, Any]) -> Dict[str, int]:
    """Create the schema, upsert all datasets and verify row counts.

    Returns:
        Mapping of table name -> rows upserted.

    Raises:
        RuntimeError: If row-count verification fails.
    """
    raw_dir = resolve_path("RAW_DATA_DIR", config["paths"]["raw_data_dir"])
    processed_dir = resolve_path("PROCESSED_DATA_DIR", config["paths"]["processed_data_dir"])
    db_path = resolve_path("DB_PATH", config["paths"]["database_path"])
    batch_size = int(config.get("pipeline", {}).get("batch_size", 500))
    db_path.parent.mkdir(parents=True, exist_ok=True)

    datasets = {
        "raw_indicators": read_raw_indicators(raw_dir, config),
        "fintech_master": read_processed(processed_dir / "fintech_master.csv"),
        "country_summary": read_processed(processed_dir / "country_summary.csv"),
    }

    logger.info("Loading into SQLite database %s", db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        create_schema(conn)
        counts = {table: upsert_dataframe(conn, table, df, batch_size) for table, df in datasets.items()}
        if not verify_counts(conn, counts):
            raise RuntimeError("Row count verification failed")
    finally:
        conn.close()
    return counts


def main() -> int:
    """CLI entry point. Returns a process exit code."""
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO"))),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        counts = load(config)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        logger.error("Loading failed: %s", exc)
        return 1
    logger.info("Loading complete: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
