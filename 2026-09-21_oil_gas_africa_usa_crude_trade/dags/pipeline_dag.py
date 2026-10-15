"""Apache Airflow DAG for the Oil & Gas Africa-USA crude trade pipeline.

This DAG orchestrates the weekly end-to-end ETL workflow:

    ingest_data  -> transform_data -> load_data -> generate_report

Each task runs the corresponding Python module via a ``BashOperator``. The
pipeline pulls seven World Bank indicators for eleven African oil producers and
the USA, transforms them into a wide-format master table, loads that table into
a SQLite database, and finally emits a short row-count report.

Key configuration:
    * dag_id: oil_gas_africa_usa_pipeline
    * schedule: @weekly
    * retries: 2 with a 5-minute delay
    * tags: oil_gas, africa, usa, world_bank
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# Resolve the project root so BashOperator commands work regardless of the
# Airflow home directory. dags/ lives directly under the project root.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

default_args = {
    "owner": "data_engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="oil_gas_africa_usa_pipeline",
    description="Weekly ETL for Africa-USA crude oil trade & production metrics",
    default_args=default_args,
    schedule_interval="@weekly",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=["oil_gas", "africa", "usa", "world_bank"],
) as dag:

    ingest_data = BashOperator(
        task_id="ingest_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/ingestion/ingest.py"
        ),
    )

    transform_data = BashOperator(
        task_id="transform_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/transformation/transform.py"
        ),
    )

    load_data = BashOperator(
        task_id="load_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/loading/load.py"
        ),
    )

    generate_report = BashOperator(
        task_id="generate_report",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            "python -c \""
            "import sqlite3; "
            "c=sqlite3.connect('data/oil_gas.db'); "
            "n=c.execute('SELECT COUNT(*) FROM oil_gas_metrics').fetchone()[0]; "
            "print(f'Report: oil_gas_metrics contains {n} rows'); "
            "c.close()\""
        ),
    )

    ingest_data >> transform_data >> load_data >> generate_report
