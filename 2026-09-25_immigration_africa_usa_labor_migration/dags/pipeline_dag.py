"""Apache Airflow DAG for the Africa-to-USA Labor Migration pipeline.

The DAG orchestrates the end-to-end ETL workflow on a Monday/Wednesday/Friday
06:00 schedule:

    ingest -> transform -> load -> generate_report

Each stage calls the ``main`` entry point of the corresponding project module.
The project ``src`` directory is added to ``sys.path`` so the modules resolve
regardless of the Airflow worker's working directory.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# Resolve project root (this file lives in <project>/dags/) and register src/.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = os.path.join(PROJECT_ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def run_ingest() -> None:
    """Execute the ingestion stage."""
    import ingest

    ingest.ingest()


def run_transform() -> None:
    """Execute the transformation stage."""
    import transform

    transform.transform()


def run_load() -> None:
    """Execute the loading stage."""
    import load

    load.load_master()


def generate_report() -> None:
    """Generate a simple text report of row counts from the database."""
    db_path = os.path.join(PROJECT_ROOT, "data", "labor_migration.db")
    report_path = os.path.join(PROJECT_ROOT, "data", "pipeline_report.txt")
    tables = [
        "countries",
        "net_migration",
        "remittances",
        "economic_indicators",
        "master_analytics",
    ]
    conn = sqlite3.connect(db_path)
    try:
        lines = [f"Labor Migration Pipeline Report - {datetime.utcnow().isoformat()}Z", ""]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            lines.append(f"{table}: {count} rows")
    finally:
        conn.close()
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="labor_migration_pipeline",
    description="Africa-to-USA labor migration and remittances ETL pipeline",
    default_args=default_args,
    schedule_interval="0 6 * * 1,3,5",  # Mon/Wed/Fri at 06:00
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["immigration", "worldbank", "etl"],
) as dag:
    ingest_task = PythonOperator(
        task_id="ingest",
        python_callable=run_ingest,
    )

    transform_task = PythonOperator(
        task_id="transform",
        python_callable=run_transform,
    )

    load_task = PythonOperator(
        task_id="load",
        python_callable=run_load,
    )

    report_task = PythonOperator(
        task_id="generate_report",
        python_callable=generate_report,
    )

    ingest_task >> transform_task >> load_task >> report_task
