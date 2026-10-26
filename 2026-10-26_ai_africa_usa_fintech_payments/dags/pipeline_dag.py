"""Airflow DAG orchestrating the AI-Driven Fintech & Digital Payments (Africa + USA) pipeline.

DAG id: ``fintech_africa_usa_pipeline``
Schedule: ``0 6 * * 1,3,5`` (Monday, Wednesday and Friday at 06:00)

Task graph::

    ingest_data >> transform_data >> load_data >> generate_report

* ``ingest_data``     - downloads the World Bank indicators into ``data/raw/``
* ``transform_data``  - cleans/merges them into ``data/processed/`` CSVs
* ``load_data``       - upserts raw + processed data into ``data/fintech_africa_usa.db``
* ``generate_report`` - writes a Markdown summary (row counts, top readiness scores,
  remittance leaders) to ``reports/pipeline_report_<run date>.md``

Each task imports the corresponding stage module and calls its ``main()``
function. A non-zero return code raises an exception so Airflow marks the task
as failed and applies the retry policy (2 retries, 5 minutes apart).
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

DB_PATH = PROJECT_ROOT / "data" / "fintech_africa_usa.db"
REPORTS_DIR = PROJECT_ROOT / "reports"

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def _run_stage(module_name: str) -> None:
    """Import a pipeline stage module and run its ``main()``; raise on failure.

    Args:
        module_name: One of ``ingest``, ``transform`` or ``load``.

    Raises:
        RuntimeError: If the stage returns a non-zero exit code.
    """
    module = __import__(module_name)
    exit_code = module.main([]) if module_name == "ingest" else module.main()
    if exit_code != 0:
        raise RuntimeError(f"Stage '{module_name}' failed with exit code {exit_code}")


def ingest_data() -> None:
    """Airflow callable: fetch World Bank indicators into data/raw/."""
    _run_stage("ingest")


def transform_data() -> None:
    """Airflow callable: build fintech_master.csv and country_summary.csv."""
    _run_stage("transform")


def load_data() -> None:
    """Airflow callable: upsert all datasets into the SQLite database."""
    _run_stage("load")


def generate_report(ds: str, **_context) -> str:
    """Airflow callable: write a Markdown run report from the SQLite database.

    Args:
        ds: Logical run date (``YYYY-MM-DD``) injected by Airflow.

    Returns:
        Path of the written report (pushed to XCom).
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"pipeline_report_{ds}.md"
    with sqlite3.connect(DB_PATH) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("raw_indicators", "fintech_master", "country_summary")}
        readiness = conn.execute(
            "SELECT country_name, ROUND(fintech_readiness_score, 1) FROM country_summary "
            "WHERE fintech_readiness_score IS NOT NULL ORDER BY fintech_readiness_score DESC LIMIT 5"
        ).fetchall()
        remittances = conn.execute(
            "SELECT country_name, remittances_usd_year, ROUND(remittances_usd / 1e9, 2), "
            "ROUND(remittance_to_gdp_ratio, 2) FROM country_summary "
            "WHERE remittances_usd IS NOT NULL ORDER BY remittances_usd DESC LIMIT 5"
        ).fetchall()

    lines = [f"# Fintech Africa-USA pipeline report - {ds}", "", "## Row counts", ""]
    lines += [f"- `{table}`: {n}" for table, n in counts.items()]
    lines += ["", "## Top 5 fintech readiness scores", "", "| Country | Score |", "|---|---|"]
    lines += [f"| {name} | {score} |" for name, score in readiness]
    lines += ["", "## Top 5 remittance recipients", "",
              "| Country | Year | US$ bn | % of GDP |", "|---|---|---|---|"]
    lines += [f"| {n} | {y} | {v} | {r if r is not None else 'n/a'} |" for n, y, v, r in remittances]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(report_path)


with DAG(
    dag_id="fintech_africa_usa_pipeline",
    description="World Bank fintech indicators for 15 African countries + USA: ingest, transform, load, report",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["fintech", "africa", "usa", "world-bank", "ai"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data", python_callable=ingest_data)
    transform_task = PythonOperator(task_id="transform_data", python_callable=transform_data)
    load_task = PythonOperator(task_id="load_data", python_callable=load_data)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
