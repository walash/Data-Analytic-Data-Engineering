"""Apache Airflow DAG for the Africa-USA Maritime Port Trade pipeline.

Defines ``maritime_ports_pipeline``, a weekly ETL workflow with four tasks:

    ingest_data -> transform_data -> load_to_db -> generate_report

Each task is a ``PythonOperator`` that calls the ``main`` function of the
corresponding project module. The project's ``src`` directory is added to
``sys.path`` so the modules resolve regardless of the Airflow worker's CWD.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

# --------------------------------------------------------------------------- #
# Make project modules importable from within Airflow workers.
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
SRC_PATH = os.path.join(PROJECT_ROOT, "src")
for _path in (PROJECT_ROOT, SRC_PATH):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _run_ingest(**_context) -> str:
    """Run the ingestion stage.

    Returns:
        str: A short status message logged by Airflow.
    """
    from ingestion.ingest import fetch_all_data, load_config

    results = fetch_all_data(load_config())
    return f"Ingested {len(results)} indicators."


def _run_transform(**_context) -> str:
    """Run the transformation stage.

    Returns:
        str: A short status message with the master dataset shape.
    """
    from transformation.transform import transform

    master = transform()
    return f"Transformed master dataset: {master.shape[0]} rows."


def _run_load(**_context) -> str:
    """Run the loading stage.

    Returns:
        str: A short status message with the database path.
    """
    from loading.load import load

    db_path = load()
    return f"Loaded database at {db_path}."


def _generate_report(**_context) -> str:
    """Generate a lightweight text report from the SQLite database.

    Queries row counts for each table and writes a timestamped report file into
    ``data/`` so downstream consumers can confirm a successful run.

    Returns:
        str: The report contents.
    """
    db_path = os.path.join(PROJECT_ROOT, "data", "maritime_ports.db")
    lines = [
        "Africa-USA Maritime Port Trade - Pipeline Report",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "-" * 48,
    ]
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        for table in ("maritime_indicators", "country_metadata",
                      "yearly_summary"):
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            lines.append(f"{table:<24}: {count} rows")
    finally:
        conn.close()

    report = "\n".join(lines)
    report_path = os.path.join(PROJECT_ROOT, "data", "pipeline_report.txt")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(report)
    return report


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="maritime_ports_pipeline",
    description="Weekly ETL for Africa-USA maritime port trade indicators.",
    default_args=default_args,
    schedule_interval="@weekly",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["maritime", "worldbank", "africa", "usa", "etl"],
) as dag:

    ingest_data = PythonOperator(
        task_id="ingest_data",
        python_callable=_run_ingest,
    )

    transform_data = PythonOperator(
        task_id="transform_data",
        python_callable=_run_transform,
    )

    load_to_db = PythonOperator(
        task_id="load_to_db",
        python_callable=_run_load,
    )

    generate_report = PythonOperator(
        task_id="generate_report",
        python_callable=_generate_report,
    )

    ingest_data >> transform_data >> load_to_db >> generate_report
