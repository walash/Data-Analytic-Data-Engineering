"""Loading stage for the Oil & Gas Africa-USA project.

Reads the processed master CSV and loads it into a SQLite database at
``data/oil_gas.db``. Creates the ``oil_gas_metrics`` table (replace strategy)
and builds indexes on the ``iso3`` and ``year`` columns for fast filtering.

Run directly with::

    python src/loading/load.py
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_CSV = PROJECT_ROOT / "data" / "processed" / "oil_gas_master.csv"
DB_PATH = PROJECT_ROOT / "data" / "oil_gas.db"
TABLE_NAME = "oil_gas_metrics"

# Explicit column -> SQLite type mapping for a clean, documented schema.
COLUMN_TYPES = {
    "iso3": "TEXT",
    "country_name": "TEXT",
    "year": "INTEGER",
    "oil_rents_pct_gdp": "REAL",
    "natural_resources_rents_pct_gdp": "REAL",
    "gdp_per_capita_usd": "REAL",
    "energy_use_kg_oil_eq": "REAL",
    "electricity_from_oil_pct": "REAL",
    "merchandise_exports_usd": "REAL",
    "fdi_inflows_usd": "REAL",
    "oil_dependency_ratio": "REAL",
    "is_african": "INTEGER",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("load")


def read_processed() -> pd.DataFrame:
    """Read the processed master CSV.

    Returns:
        DataFrame of the master table.

    Raises:
        FileNotFoundError: If the processed CSV is missing.
    """
    if not PROCESSED_CSV.exists():
        raise FileNotFoundError(
            f"Processed file not found: {PROCESSED_CSV}. Run transform.py first."
        )
    df = pd.read_csv(PROCESSED_CSV)
    logger.info("Read processed CSV: %d rows, %d columns", len(df), len(df.columns))
    return df


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the target table with an explicit schema (drop if exists).

    Args:
        conn: Open SQLite connection.
    """
    cols_sql = ",\n    ".join(
        f"{name} {sqltype}" for name, sqltype in COLUMN_TYPES.items()
    )
    conn.execute(f"DROP TABLE IF EXISTS {TABLE_NAME}")
    conn.execute(
        f"CREATE TABLE {TABLE_NAME} (\n    {cols_sql}\n)"
    )
    conn.commit()
    logger.info("Created table %s", TABLE_NAME)


def load_dataframe(df: pd.DataFrame, conn: sqlite3.Connection) -> int:
    """Load the DataFrame into the SQLite table (append into fresh schema).

    Args:
        df: The master DataFrame.
        conn: Open SQLite connection.

    Returns:
        Number of rows written.
    """
    # Keep only known columns, in schema order.
    ordered_cols = [c for c in COLUMN_TYPES if c in df.columns]
    df = df[ordered_cols]
    df.to_sql(TABLE_NAME, conn, if_exists="append", index=False)
    conn.commit()
    return len(df)


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create indexes on ``iso3`` and ``year`` for query performance.

    Args:
        conn: Open SQLite connection.
    """
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_iso3 "
        f"ON {TABLE_NAME} (iso3)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_year "
        f"ON {TABLE_NAME} (year)"
    )
    conn.commit()
    logger.info("Created indexes on iso3 and year")


def load() -> int:
    """Run the full loading pipeline.

    Returns:
        The final row count in the database table.
    """
    df = read_processed()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)
        written = load_dataframe(df, conn)
        create_indexes(conn)
        count = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    finally:
        conn.close()

    logger.info("Loaded %d rows into %s (table row count: %d)", written, DB_PATH, count)
    print(f"[load] {count} rows loaded into {TABLE_NAME} at {DB_PATH}")
    return count


def main() -> None:
    """Entry point."""
    load()


if __name__ == "__main__":
    main()
