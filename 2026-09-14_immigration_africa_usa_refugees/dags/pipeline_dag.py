"""Apache Airflow DAG for the Africa-to-USA immigration data pipeline.

The DAG orchestrates the full ETL workflow on a Mon/Wed/Fri 06:00 schedule:

    ingest_data -> transform_data -> load_data -> generate_report

Each task calls the ``main`` entry point of the corresponding project module.
The project ``src`` directory is added to ``sys.path`` so the modules resolve
whether the DAG runs from an Airflow worker or a local scheduler.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# --------------------------------------------------------------------------- #
# Make the project modules importable from within Airflow workers
# --------------------------------------------------------------------------- #
# dags/pipeline_dag.py -> project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parents[1]
for sub in ("", "src"):
    candidate = str(PROJECT_ROOT / sub) if sub else str(PROJECT_ROOT)
    if candidate not in sys.path:
        sys.path.insert(0, candidate)


# --------------------------------------------------------------------------- #
# Task callables
# --------------------------------------------------------------------------- #
def run_ingest(**_context) -> str:
    """Run the ingestion stage.

    Returns:
        A short status string for the Airflow logs / XCom.
    """
    from ingestion.ingest import ingest

    ingest()
    return "ingest_complete"


def run_transform(**_context) -> str:
    """Run the transformation stage.

    Returns:
        A short status string for the Airflow logs / XCom.
    """
    from transformation.transform import transform

    transform()
    return "transform_complete"


def run_load(**_context) -> str:
    """Run the loading stage.

    Returns:
        A short status string for the Airflow logs / XCom.
    """
    from loading.load import load

    load()
    return "load_complete"


def generate_report(**_context) -> str:
    """Generate a lightweight text report of row counts per table.

    Reads the SQLite database populated by the load stage and writes a
    timestamped summary into ``data/processed/pipeline_report.txt``.

    Returns:
        The path to the report file.
    """
    import sqlite3

    db_path = PROJECT_ROOT / "data" / "immigration_analytics.db"
    report_path = PROJECT_ROOT / "data" / "processed" / "pipeline_report.txt"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    tables = [
        "african_migration", "remittances", "population",
        "asylum_decisions", "country_summary",
    ]
    lines = [
        "Africa-to-USA Immigration Pipeline Report",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "-" * 45,
    ]
    conn = sqlite3.connect(str(db_path))
    try:
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            lines.append(f"{table:<20} {count:>8} rows")
    finally:
        conn.close()

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(report_path)


# --------------------------------------------------------------------------- #
# DAG definition
# --------------------------------------------------------------------------- #
default_args = {
    "owner": "data_engineering",
    "depends_on_past": False,
    "email": ["data-eng@example.com"],
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="immigration_africa_usa_pipeline",
    description="ETL for Africa-to-USA immigration, remittance and refugee data",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="0 6 * * 1,3,5",  # Mon/Wed/Fri at 06:00
    catchup=False,
    max_active_runs=1,
    tags=["immigration", "africa", "usa", "etl"],
) as dag:

    ingest_task = PythonOperator(
        task_id="ingest_data",
        python_callable=run_ingest,
    )

    transform_task = PythonOperator(
        task_id="transform_data",
        python_callable=run_transform,
    )

    load_task = PythonOperator(
        task_id="load_data",
        python_callable=run_load,
    )

    report_task = PythonOperator(
        task_id="generate_report",
        python_callable=generate_report,
    )

    ingest_task >> transform_task >> load_task >> report_task
