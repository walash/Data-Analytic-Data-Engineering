"""Apache Airflow DAG orchestrating the Inland Waterways (Africa & USA) pipeline.

Weekly run: ingest_data >> transform_data >> load_data >> generate_report

* ingest_data     - World Bank API pull + curated waterway datasets -> data/raw/
* transform_data  - cleaning, enrichment and merges -> data/processed/
* load_data       - processed CSVs -> SQLite warehouse data/waterways.db
* generate_report - writes a plain-text summary of table counts and key
                    utilization metrics to data/reports/
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.getenv("PIPELINE_HOME", Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("DB_PATH", PROJECT_ROOT / "data" / "waterways.db"))
REPORT_DIR = Path(os.getenv("DATA_DIR", PROJECT_ROOT / "data")) / "reports"


def run_ingestion(**_context):
    import ingest
    summary = ingest.ingest(dry_run=False)
    logger.info("Ingestion summary: %s", summary)
    return summary


def run_transformation(**_context):
    import transform
    summary = transform.transform()
    logger.info("Transformation summary: %s", summary)
    return summary


def run_loading(**_context):
    import load
    summary = load.load()
    logger.info("Loading summary: %s", summary)
    return summary


def generate_report(**context):
    """Write a text report with row counts and the Africa-vs-USA utilization gap."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_date = context.get("ds", datetime.utcnow().strftime("%Y-%m-%d"))
    tables = ["waterway_infrastructure", "us_freight", "africa_freight",
              "container_traffic", "country_metrics"]
    lines = [
        "Inland Waterways Pipeline Report - run {}".format(run_date),
        "=" * 60,
        "Database: {}".format(DB_PATH),
        "",
        "Table row counts:",
    ]
    conn = sqlite3.connect(str(DB_PATH))
    try:
        for table in tables:
            count = conn.execute("SELECT COUNT(*) FROM {}".format(table)).fetchone()[0]
            lines.append("  {:<26}{:>8,}".format(table, count))
        lines += ["", "Freight per navigable km (2015-2022, tons/km):"]
        regional = conn.execute(
            """
            SELECT region,
                   SUM(waterway_freight_thousand_tons) * 1000.0 / SUM(navigable_km)
            FROM country_metrics
            WHERE waterway_freight_thousand_tons IS NOT NULL
              AND navigable_km > 0 AND year BETWEEN 2015 AND 2022
            GROUP BY region ORDER BY region
            """
        ).fetchall()
        for region, ratio in regional:
            lines.append("  {:<26}{:>10,.1f}".format(region, ratio))
        ratios = dict(regional)
        if ratios.get("Africa"):
            lines.append("  USA / Africa gap multiple: {:.1f}x".format(ratios.get("USA", 0) / ratios["Africa"]))
        lines += ["", "Top African corridors by latest-year freight (thousand tons):"]
        for row in conn.execute(
            """
            SELECT country, waterway, year, freight_thousand_tons
            FROM africa_freight a
            WHERE year = (SELECT MAX(year) FROM africa_freight b
                          WHERE b.iso3 = a.iso3 AND b.waterway = a.waterway)
            ORDER BY freight_thousand_tons DESC
            """
        ):
            lines.append("  {:<10} {:<14} {}  {:>8,.0f}".format(*row))
    finally:
        conn.close()

    report_path = REPORT_DIR / "pipeline_report_{}.txt".format(run_date)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="inland_waterways_pipeline",
    description="Africa & USA inland waterway freight ETL (World Bank, USACE, AfDB)",
    default_args=default_args,
    schedule_interval="@weekly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["transportation", "waterways", "africa", "usa"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=run_ingestion)
    transform_data = PythonOperator(task_id="transform_data", python_callable=run_transformation)
    load_data = PythonOperator(task_id="load_data", python_callable=run_loading)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_data >> transform_data >> load_data >> report_task
