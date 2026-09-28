"""Loading module for the AI in Agriculture project.

Loads the feature-engineered CSV produced by the transformation stage into a
SQLite database (``data/agriculture_analytics.db``). Two tables are created:

    * agriculture_metrics -> one row per country-year with all metrics/features
    * country_summary     -> per-country aggregates for fast analytics

Run:
    python src/loading/load.py
"""

import logging
import os
import sqlite3
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("loading")

DB_PATH = os.environ.get("DB_PATH", "data/agriculture_analytics.db")

CREATE_METRICS = """
CREATE TABLE IF NOT EXISTS agriculture_metrics (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    country_code           TEXT,
    country_name           TEXT,
    year                   INTEGER,
    region                 TEXT,
    cereal_yield_kg_ha     REAL,
    agr_land_pct           REAL,
    food_production_index  REAL,
    fertilizer_kg_ha       REAL,
    agr_value_added_pct    REAL,
    rural_population_pct    REAL,
    yield_growth_rate      REAL,
    fertilizer_efficiency  REAL,
    food_security_score    REAL,
    ai_investment_priority TEXT
);
"""

CREATE_SUMMARY = """
CREATE TABLE IF NOT EXISTS country_summary (
    country_code            TEXT PRIMARY KEY,
    country_name            TEXT,
    region                  TEXT,
    years_covered           INTEGER,
    avg_cereal_yield_kg_ha  REAL,
    avg_food_production_idx REAL,
    avg_fertilizer_kg_ha    REAL,
    avg_agr_value_added_pct REAL,
    avg_rural_population_pct REAL,
    avg_food_security_score REAL,
    avg_yield_growth_rate   REAL,
    ai_investment_priority  TEXT
);
"""

METRIC_COLUMNS = [
    "country_code",
    "country_name",
    "year",
    "region",
    "cereal_yield_kg_ha",
    "agr_land_pct",
    "food_production_index",
    "fertilizer_kg_ha",
    "agr_value_added_pct",
    "rural_population_pct",
    "yield_growth_rate",
    "fertilizer_efficiency",
    "food_security_score",
    "ai_investment_priority",
]


def create_schema(conn):
    """Create (or reset) the analytics tables."""
    logger.info("Creating database schema...")
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS agriculture_metrics;")
    cur.execute("DROP TABLE IF EXISTS country_summary;")
    cur.execute(CREATE_METRICS)
    cur.execute(CREATE_SUMMARY)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_country "
        "ON agriculture_metrics(country_name);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_year "
        "ON agriculture_metrics(year);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_metrics_region "
        "ON agriculture_metrics(region);"
    )
    conn.commit()
    logger.info("Schema created.")


def load_agriculture_data(conn, processed_dir):
    """Load agriculture_with_features.csv into agriculture_metrics.

    Returns:
        Number of rows inserted.
    """
    processed_dir = Path(processed_dir)
    csv_path = processed_dir / "agriculture_with_features.csv"
    if not csv_path.exists():
        logger.error("Feature CSV not found: %s", csv_path)
        return 0

    df = pd.read_csv(csv_path)

    # Ensure all expected columns exist.
    for col in METRIC_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[METRIC_COLUMNS]

    df.to_sql("agriculture_metrics", conn, if_exists="append", index=False)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM agriculture_metrics;").fetchone()[0]
    logger.info("Inserted %d rows into agriculture_metrics", count)
    return count


def build_country_summary(conn):
    """Aggregate agriculture_metrics into the country_summary table.

    Returns:
        Number of summary rows inserted.
    """
    logger.info("Building country_summary aggregates...")
    conn.execute("DELETE FROM country_summary;")
    insert_sql = """
    INSERT INTO country_summary (
        country_code, country_name, region, years_covered,
        avg_cereal_yield_kg_ha, avg_food_production_idx, avg_fertilizer_kg_ha,
        avg_agr_value_added_pct, avg_rural_population_pct,
        avg_food_security_score, avg_yield_growth_rate, ai_investment_priority
    )
    SELECT
        country_code,
        country_name,
        region,
        COUNT(DISTINCT year)              AS years_covered,
        ROUND(AVG(cereal_yield_kg_ha), 2) AS avg_cereal_yield_kg_ha,
        ROUND(AVG(food_production_index), 2) AS avg_food_production_idx,
        ROUND(AVG(fertilizer_kg_ha), 2)   AS avg_fertilizer_kg_ha,
        ROUND(AVG(agr_value_added_pct), 2) AS avg_agr_value_added_pct,
        ROUND(AVG(rural_population_pct), 2) AS avg_rural_population_pct,
        ROUND(AVG(food_security_score), 2) AS avg_food_security_score,
        ROUND(AVG(yield_growth_rate), 2)  AS avg_yield_growth_rate,
        MAX(ai_investment_priority)       AS ai_investment_priority
    FROM agriculture_metrics
    WHERE country_code IS NOT NULL
    GROUP BY country_code, country_name, region;
    """
    conn.execute(insert_sql)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM country_summary;").fetchone()[0]
    logger.info("Inserted %d rows into country_summary", count)
    return count


def main():
    """Create the database, load metrics and build summaries."""
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    processed_dir = data_dir / "processed"
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Opening SQLite database at %s", db_path.resolve())
    conn = sqlite3.connect(str(db_path))
    try:
        create_schema(conn)
        metric_rows = load_agriculture_data(conn, processed_dir)
        summary_rows = build_country_summary(conn)
        logger.info(
            "Loading complete: %d metric rows, %d country summaries",
            metric_rows,
            summary_rows,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
