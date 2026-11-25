"""Airflow DAG for the AI-Powered EdTech & Learning Analytics pipeline.

Runs Monday, Wednesday and Friday at 06:00 and executes:

    ingest_data >> transform_data >> load_data >> generate_report

* ingest_data     - pulls 5 World Bank indicators for 15 African
                    countries + USA into data/raw/
* transform_data  - cleans, merges and scores the data into
                    data/processed/
* load_data       - loads processed data into data/education_analytics.db
* generate_report - writes a plain-text summary (row counts, top
                    EdTech-ready countries, Africa vs USA averages)
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ingestion import ingest as ingest_module  # noqa: E402
from src.loading import load as load_module  # noqa: E402
from src.transformation import transform as transform_module  # noqa: E402


def run_ingestion(**_context):
    """Airflow callable for the ingestion stage.

    Returns:
        dict: Rows fetched per indicator (pushed to XCom).
    """
    config = ingest_module.load_config()
    ingest_module.setup_logging(config)
    return ingest_module.ingest(config)


def run_transformation(**_context):
    """Airflow callable for the transformation stage.

    Returns:
        dict: Row counts of the processed outputs.
    """
    return transform_module.transform(transform_module.load_config())


def run_loading(**_context):
    """Airflow callable for the loading stage.

    Returns:
        dict: Row counts per SQLite table.
    """
    return load_module.load(load_module.load_config())


def generate_report(**context):
    """Write a text report summarising the loaded database.

    Args:
        context: Airflow task context (used for the logical date).

    Returns:
        str: Path of the written report.
    """
    config = load_module.load_config()
    db_path = PROJECT_ROOT / config["paths"]["database_path"]
    report_path = PROJECT_ROOT / config["paths"].get(
        "report_path", "data/processed/pipeline_report.txt")
    run_date = context.get("ds", datetime.utcnow().strftime("%Y-%m-%d"))

    lines = [
        "EdTech & Learning Analytics Pipeline Report",
        f"Run date: {run_date}",
        "",
        "Table row counts:",
    ]
    with sqlite3.connect(db_path) as conn:
        for table in ("raw_indicators", "edtech_readiness",
                      "country_summary"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            lines.append(f"  {table:<18} {count[0]:>6}")

        lines += ["", "Top 5 countries by EdTech readiness score:"]
        top = conn.execute(
            "SELECT readiness_rank, country_name, edtech_readiness_score, "
            "readiness_components FROM country_summary "
            "ORDER BY edtech_readiness_score DESC LIMIT 5").fetchall()
        for rank, name, score, comps in top:
            lines.append(f"  {rank}. {name:<22} {score:6.2f} "
                         f"({comps}/3 components)")

        lines += ["", "Average latest values by region:"]
        regions = conn.execute(
            "SELECT region, AVG(internet_users_pct), "
            "AVG(primary_completion_rate), AVG(tertiary_enrollment_rate), "
            "AVG(education_expenditure_pct_gdp) "
            "FROM country_summary GROUP BY region").fetchall()
        for region, inet, pcr, ter, spend in regions:
            lines.append(
                f"  {region:<7} internet={inet or 0:5.1f}% "
                f"primary_completion={pcr or 0:5.1f}% "
                f"tertiary={ter or 0:5.1f}% spend={spend or 0:4.2f}% GDP")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return str(report_path)


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 11, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="edtech_learning_analytics_pipeline",
    description="World Bank education & digital-access ETL for Africa + USA",
    default_args=default_args,
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["edtech", "education", "africa", "usa", "world-bank"],
) as dag:
    ingest_task = PythonOperator(
        task_id="ingest_data", python_callable=run_ingestion)
    transform_task = PythonOperator(
        task_id="transform_data", python_callable=run_transformation)
    load_task = PythonOperator(
        task_id="load_data", python_callable=run_loading)
    report_task = PythonOperator(
        task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
