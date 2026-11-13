"""Airflow DAG orchestrating the Africa vs USA NCD (diabetes & CVD) pipeline.

Task graph::

    ingest_worldbank_ncd >> transform_ncd_data >> load_ncd_sqlite >> generate_report

Schedule: 06:00 UTC every Monday, Wednesday and Friday (``0 6 * * 1,3,5``).
Each task imports the corresponding pipeline module from ``src/`` and calls
its stage function, so the same code runs under Airflow, Docker and the CLI.

Set the ``NCD_PROJECT_ROOT`` environment variable (or Airflow Variable of the
same name) when the project is not located next to the ``dags/`` folder.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(
    os.environ.get("NCD_PROJECT_ROOT", Path(__file__).resolve().parents[1])
)
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

logger = logging.getLogger("ncd.dag")

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=30),
}


def run_ingest(**_context) -> dict:
    """Fetch all configured World Bank NCD indicators into data/raw/."""
    import ingest

    config = ingest.load_config()
    status = ingest.ingest(config)
    if status and all(s == "failed" for s in status.values()):
        raise RuntimeError(f"Ingestion failed for every indicator: {status}")
    return status


def run_transform(**_context) -> dict:
    """Clean and enrich raw CSVs into data/processed/."""
    import transform

    written = transform.transform(transform.load_config())
    return {name: str(path) for name, path in written.items()}


def run_load(**_context) -> dict:
    """Create the SQLite star schema and load processed tables."""
    import load

    return load.load(load.load_config())


def generate_report(**context) -> str:
    """Write a Markdown run report with row counts and headline NCD metrics."""
    import load

    config = load.load_config()
    db_path = load.database_path(config)
    report_dir = PROJECT_ROOT / config["paths"].get("report_dir", "data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    run_date = context.get("ds", datetime.utcnow().strftime("%Y-%m-%d"))

    conn = sqlite3.connect(db_path)
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("dim_country", "dim_indicator", "fact_ncd_observation")
        }
        snapshot = conn.execute(
            """
            SELECT indicator_key, region_group, COUNT(*), ROUND(AVG(value), 2), MAX(year)
            FROM v_africa_usa_ncd
            WHERE is_latest = 1 AND region_group IN ('Africa', 'USA')
            GROUP BY indicator_key, region_group
            ORDER BY indicator_key, region_group
            """
        ).fetchall()
        top_africa = conn.execute(
            """
            SELECT country_name, ROUND(value, 1), year
            FROM v_africa_usa_ncd
            WHERE is_latest = 1 AND region_group = 'Africa'
              AND indicator_key = 'diabetes_prevalence'
            ORDER BY value DESC LIMIT 5
            """
        ).fetchall()
    finally:
        conn.close()

    lines = [
        f"# NCD Pipeline Report - {run_date}",
        "",
        "## Table row counts",
        *[f"- {table}: {n}" for table, n in counts.items()],
        "",
        "## Latest-year averages (Africa vs USA)",
        "| indicator | region | entities | avg % | latest year |",
        "|---|---|---|---|---|",
        *[f"| {k} | {r} | {n} | {v} | {y} |" for k, r, n, v, y in snapshot],
        "",
        "## Top 5 African countries by diabetes prevalence",
        *[f"{i}. {name}: {val}% ({yr})" for i, (name, val, yr) in enumerate(top_africa, 1)],
    ]
    report_path = report_dir / f"ncd_report_{run_date}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


with DAG(
    dag_id="africa_usa_ncd_diabetes_cardiovascular_pipeline",
    description="World Bank NCD indicators (diabetes, NCD deaths) - Africa vs USA ETL",
    default_args=DEFAULT_ARGS,
    start_date=datetime(2026, 11, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["health", "ncd", "diabetes", "cardiovascular", "africa", "usa", "worldbank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_worldbank_ncd", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform_ncd_data", python_callable=run_transform)
    load_task = PythonOperator(task_id="load_ncd_sqlite", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
