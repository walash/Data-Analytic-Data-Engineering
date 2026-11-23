"""Airflow DAG orchestrating the Africa-USA skilled worker migration pipeline.

Weekly run: ingest_data -> transform_data -> load_data -> generate_report.
Each task wraps the corresponding stage module in ``src/`` with a
PythonOperator. The project root defaults to the parent of this ``dags/``
folder and can be overridden with the ``MIGRATION_PROJECT_ROOT`` env var
(e.g. when the DAG file is copied into ``$AIRFLOW_HOME/dags``).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.getenv("MIGRATION_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for stage in ("ingestion", "transformation", "loading"):
    stage_dir = str(PROJECT_ROOT / "src" / stage)
    if stage_dir not in sys.path:
        sys.path.insert(0, stage_dir)


def _config() -> dict:
    """Load the project configuration (imported lazily to keep DAG parsing fast)."""
    import ingest

    return ingest.load_config(os.getenv("CONFIG_PATH") or PROJECT_ROOT / "config" / "config.yaml")


def ingest_data(**context) -> str:
    """Fetch World Bank indicators for 10 African countries and the USA."""
    import ingest

    path = ingest.ingest(_config())
    return str(path)


def transform_data(**context) -> dict:
    """Clean raw indicators and compute derived migration metrics."""
    import transform

    return {name: str(path) for name, path in transform.transform(_config()).items()}


def load_data(**context) -> str:
    """Load processed CSVs into the SQLite analytics database."""
    import load

    return str(load.load(_config()))


def generate_report(**context) -> str:
    """Write the Markdown run report summarising the loaded database."""
    import load

    return str(load.generate_report(_config()))


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="africa_usa_skilled_worker_migration_pipeline",
    description="World Bank migration, remittance and education indicators for Africa-USA skilled worker flows",
    default_args=default_args,
    start_date=datetime(2026, 11, 23),
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["immigration", "africa", "usa", "h1b", "world-bank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data", python_callable=ingest_data)
    transform_task = PythonOperator(task_id="transform_data", python_callable=transform_data)
    load_task = PythonOperator(task_id="load_data", python_callable=load_data)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
