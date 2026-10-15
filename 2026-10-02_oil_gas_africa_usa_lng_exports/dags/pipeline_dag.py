"""Apache Airflow DAG for the Africa LNG Exports to the USA pipeline.

Runs weekly: ``ingest_data >> transform_data >> load_data >> generate_report``.
Each task is a PythonOperator calling the stage module in ``src/``. The
project root is resolved from this file (or the ``LNG_PROJECT_ROOT`` env var)
and put on ``sys.path`` so the modules import cleanly on Airflow workers.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(
    os.getenv("LNG_PROJECT_ROOT", Path(__file__).resolve().parents[1])
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _module(name: str):
    """Import a pipeline stage module lazily (keeps DAG parsing fast)."""
    return importlib.import_module(name)


def run_ingestion(**_context) -> dict:
    """Download UN Comtrade LNG trade and World Bank indicators."""
    paths = _module("src.ingestion.ingest").ingest()
    return {name: str(path) for name, path in paths.items()}


def run_transformation(**_context) -> dict:
    """Clean, reshape and enrich raw data into processed CSVs."""
    paths = _module("src.transformation.transform").transform()
    return {name: str(path) for name, path in paths.items()}


def run_loading(**_context) -> dict:
    """Upsert processed data into the SQLite warehouse."""
    return _module("src.loading.load").load()


def run_report(**context) -> str:
    """Write the pipeline summary report and log the loaded row counts."""
    counts = context["ti"].xcom_pull(task_ids="load_data") or {}
    report = _module("src.loading.load").generate_report()
    print(f"Row counts from load_data: {counts}")
    print(report.read_text(encoding="utf-8"))
    return str(report)


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="lng_exports_pipeline",
    description="Africa LNG exports to the USA: Comtrade + World Bank ETL",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["oil-gas", "lng", "africa", "usa", "comtrade", "worldbank"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data",
                                 python_callable=run_ingestion)
    transform_data = PythonOperator(task_id="transform_data",
                                    python_callable=run_transformation)
    load_data = PythonOperator(task_id="load_data",
                               python_callable=run_loading)
    generate_report = PythonOperator(task_id="generate_report",
                                     python_callable=run_report)

    ingest_data >> transform_data >> load_data >> generate_report
