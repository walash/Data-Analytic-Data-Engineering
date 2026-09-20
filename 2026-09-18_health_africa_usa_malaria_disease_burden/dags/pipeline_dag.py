"""Apache Airflow DAG for the Malaria & Infectious Disease Burden pipeline.

Orchestrates the end-to-end ETL weekly:

    ingest_data -> transform_data -> load_to_db -> generate_report

Each ETL stage runs the corresponding project module via a ``BashOperator``.
The final task prints a short row-count report from the SQLite database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

# Resolve the project root relative to this DAG file so the BashOperators can
# invoke the project modules regardless of where Airflow places the dags folder.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 9, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="health_disease_burden_pipeline",
    default_args=default_args,
    description="Weekly ETL for malaria & infectious disease burden (Africa vs USA).",
    schedule_interval="@weekly",
    catchup=False,
    tags=["health", "malaria", "africa", "usa", "etl"],
) as dag:

    ingest_data = BashOperator(
        task_id="ingest_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && python -m src.ingestion.ingest"
        ),
    )

    transform_data = BashOperator(
        task_id="transform_data",
        bash_command=(
            f"cd {PROJECT_ROOT} && python -m src.transformation.transform"
        ),
    )

    load_to_db = BashOperator(
        task_id="load_to_db",
        bash_command=(
            f"cd {PROJECT_ROOT} && python -m src.loading.load"
        ),
    )

    generate_report = BashOperator(
        task_id="generate_report",
        bash_command=(
            f"cd {PROJECT_ROOT} && python -c \""
            "import sqlite3;"
            "c=sqlite3.connect('data/health_disease_burden.db');"
            "cur=c.cursor();"
            "tables=['malaria_incidence','malaria_deaths','health_indicators','country_metadata'];"
            "print('=== health_disease_burden report ===');"
            "[print(t, cur.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]) for t in tables];"
            "c.close()\""
        ),
    )

    ingest_data >> transform_data >> load_to_db >> generate_report
