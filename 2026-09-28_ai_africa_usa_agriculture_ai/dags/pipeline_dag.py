"""Apache Airflow DAG for the AI in Agriculture data pipeline.

Orchestrates the end-to-end ETL workflow on a Mon/Wed/Fri schedule:

    ingest -> transform -> load -> generate_report -> validate

Each stage runs the corresponding module from ``src/`` and a final validation
step confirms the SQLite database was populated.
"""

import os
import sqlite3
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# Project root: two levels up from this DAG file (dags/ -> project root).
PROJECT_ROOT = os.environ.get(
    "PROJECT_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
)
DB_PATH = os.path.join(PROJECT_ROOT, "data", "agriculture_analytics.db")

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email": ["alerts@example.com"],
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def generate_report(**context):
    """Generate a text summary report of the loaded database."""
    report_dir = os.path.join(PROJECT_ROOT, "data", "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "pipeline_report.txt")

    lines = ["AI in Agriculture — Pipeline Report", "=" * 40, ""]
    lines.append(f"Generated: {datetime.utcnow().isoformat()} UTC")

    if not os.path.exists(DB_PATH):
        lines.append(f"ERROR: database not found at {DB_PATH}")
    else:
        conn = sqlite3.connect(DB_PATH)
        try:
            metric_rows = conn.execute(
                "SELECT COUNT(*) FROM agriculture_metrics;"
            ).fetchone()[0]
            summary_rows = conn.execute(
                "SELECT COUNT(*) FROM country_summary;"
            ).fetchone()[0]
            regions = conn.execute(
                "SELECT region, COUNT(DISTINCT country_name) "
                "FROM agriculture_metrics GROUP BY region;"
            ).fetchall()
            lines.append(f"agriculture_metrics rows: {metric_rows}")
            lines.append(f"country_summary rows: {summary_rows}")
            lines.append("")
            lines.append("Countries per region:")
            for region, n in regions:
                lines.append(f"  - {region}: {n} countries")
        finally:
            conn.close()

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    return report_path


with DAG(
    dag_id="agriculture_ai_pipeline",
    description="ETL pipeline for AI in Agriculture (Africa & USA)",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["agriculture", "ai", "etl", "worldbank"],
) as dag:

    ingest_task = BashOperator(
        task_id="ingest",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/ingestion/ingest.py"
        ),
    )

    transform_task = BashOperator(
        task_id="transform",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/transformation/transform.py"
        ),
    )

    load_task = BashOperator(
        task_id="load",
        bash_command=(
            f"cd {PROJECT_ROOT} && python src/loading/load.py"
        ),
    )

    generate_report_task = PythonOperator(
        task_id="generate_report",
        python_callable=generate_report,
    )

    validate_task = BashOperator(
        task_id="validate",
        bash_command=(
            f"cd {PROJECT_ROOT} && "
            f"python -c \"import sqlite3, sys; "
            f"c = sqlite3.connect('{DB_PATH}'); "
            f"n = c.execute('SELECT COUNT(*) FROM agriculture_metrics').fetchone()[0]; "
            f"c.close(); "
            f"print('Validation rows:', n); "
            f"sys.exit(0 if n > 0 else 1)\""
        ),
    )

    ingest_task >> transform_task >> load_task >> generate_report_task >> validate_task
