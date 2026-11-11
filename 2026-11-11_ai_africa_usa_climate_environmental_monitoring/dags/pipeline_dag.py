"""Airflow DAG for the AI-Driven Climate & Environmental Monitoring pipeline.

Runs Monday, Wednesday and Friday at 06:00 (``0 6 * * 1,3,5``):

    ingest_data -> transform_data -> load_data -> generate_report

* ingest_data     - pulls 5 World Bank indicators for 29 African countries + USA
* transform_data  - cleans, pivots, engineers features, LinearRegression forecast
* load_data       - loads processed CSVs into SQLite (data/climate_monitoring.db)
* generate_report - writes a text summary of table counts and key metrics to
                    data/reports/

Set the ``CLIMATE_PROJECT_ROOT`` environment variable if the project is not
located one directory above this DAG file.
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.getenv("CLIMATE_PROJECT_ROOT",
                              Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

logger = logging.getLogger(__name__)

default_args = {
    "owner": "climate-data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 11, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def run_ingestion():
    """Fetch raw World Bank indicator data into data/raw/.

    Returns:
        dict: Rows written per indicator.
    """
    import ingest

    config = ingest.load_config()
    summary = ingest.ingest(config, dry_run=False)
    logger.info("Ingestion summary: %s", summary)
    return summary


def run_transformation():
    """Clean raw data, build features and ML forecasts into data/processed/.

    Returns:
        dict: Rows written per processed file.
    """
    import transform

    config = transform.load_config()
    summary = transform.transform(config)
    logger.info("Transformation summary: %s", summary)
    return summary


def run_loading():
    """Load processed CSVs into the SQLite warehouse.

    Returns:
        dict: Rows loaded per table.
    """
    import load

    config = load.load_config()
    summary = load.load(config)
    logger.info("Loading summary: %s", summary)
    return summary


def generate_report(**context):
    """Write a plain-text run report summarising the warehouse contents.

    Args:
        **context: Airflow task context (used for the logical date).

    Returns:
        str: Path of the report file.

    Raises:
        FileNotFoundError: If the SQLite database does not exist.
    """
    db_path = PROJECT_ROOT / "data" / "climate_monitoring.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    report_dir = PROJECT_ROOT / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    run_date = context.get("ds", datetime.utcnow().strftime("%Y-%m-%d"))

    with sqlite3.connect(db_path) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("climate_indicators", "country_profiles",
                          "ml_predictions")
        }
        regional = conn.execute(
            """
            SELECT region,
                   ROUND(AVG(latest_forest_area_pct), 2),
                   ROUND(AVG(latest_renewable_energy_pct), 2),
                   ROUND(AVG(latest_electricity_access_pct), 2)
            FROM country_profiles GROUP BY region ORDER BY region
            """
        ).fetchall()
        low_access = conn.execute(
            """
            SELECT country_name, ROUND(latest_electricity_access_pct, 1)
            FROM country_profiles
            WHERE latest_electricity_access_pct < 50
            ORDER BY latest_electricity_access_pct
            """
        ).fetchall()
        forecasts = conn.execute(
            """
            SELECT country_name, ROUND(predicted_renewable_energy_pct, 1)
            FROM ml_predictions WHERE forecast_year = 2027
            ORDER BY predicted_renewable_energy_pct DESC LIMIT 5
            """
        ).fetchall()

    lines = [
        "Climate & Environmental Monitoring - Africa and USA",
        f"Run date: {run_date}",
        "",
        "Table row counts:",
        *[f"  {t}: {n}" for t, n in counts.items()],
        "",
        "Regional averages (forest %, renewable %, electricity access %):",
        *[f"  {r}: {f} | {re} | {a}" for r, f, re, a in regional],
        "",
        "Countries below 50% electricity access:",
        *[f"  {c}: {v}%" for c, v in low_access],
        "",
        "Top 5 forecast renewable shares for 2027 (LinearRegression):",
        *[f"  {c}: {v}%" for c, v in forecasts],
    ]
    report_path = report_dir / f"pipeline_report_{run_date}.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


with DAG(
    dag_id="climate_monitoring_pipeline",
    description="World Bank climate indicators ETL + ML for Africa and USA",
    default_args=default_args,
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["climate", "africa", "usa", "world-bank", "ml"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data",
                                 python_callable=run_ingestion)
    transform_task = PythonOperator(task_id="transform_data",
                                    python_callable=run_transformation)
    load_task = PythonOperator(task_id="load_data",
                               python_callable=run_loading)
    report_task = PythonOperator(task_id="generate_report",
                                 python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
