"""Airflow DAG: Deepwater Offshore Oil Production - Africa vs USA Gulf of Mexico.

Task flow (Mon/Wed/Fri at 06:00 UTC)::

    ingest_data -> transform_data -> load_data -> generate_report

* ingest_data     - src/ingestion/ingest.py   (OWID CSV + World Bank API)
* transform_data  - src/transformation/transform.py
* load_data       - src/loading/load.py       (SQLite data/oil_production.db)
* generate_report - writes data/reports/pipeline_report_<ds>.txt with row
                    counts and headline Africa-vs-USA production metrics.

The Airflow import is wrapped in try/except so the module can be imported
and linted (flake8, CI smoke tests) on machines without Airflow installed;
in that case ``dag`` is ``None`` and the callables can be run directly via
``python dags/pipeline_dag.py``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from airflow import DAG
    from airflow.operators.python import PythonOperator

    AIRFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without Airflow
    DAG = None
    PythonOperator = None
    AIRFLOW_AVAILABLE = False

logger = logging.getLogger("oil_pipeline_dag")

DB_PATH = Path(os.getenv("DB_PATH", PROJECT_ROOT / "data" / "oil_production.db"))
REPORT_DIR = PROJECT_ROOT / "data" / "reports"

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 1, 1),
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
    "email_on_retry": False,
}


def run_ingestion(**_: Any) -> dict[str, Any]:
    """Download OWID and World Bank data into data/raw/."""
    from src.ingestion.ingest import ingest

    return ingest()


def run_transformation(**_: Any) -> dict[str, Any]:
    """Clean and enrich raw data into data/processed/."""
    from src.transformation.transform import transform

    return transform()


def run_loading(**_: Any) -> dict[str, int]:
    """Load processed CSVs into the SQLite database."""
    from src.loading.load import load

    return load()


def generate_report(**context: Any) -> str:
    """Write a plain-text run report with row counts and headline metrics."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    run_date = context.get("ds") or datetime.utcnow().strftime("%Y-%m-%d")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("oil_production", "oil_rents")
        }
        latest_year = conn.execute(
            "SELECT MAX(year) FROM oil_production WHERE has_production = 1"
        ).fetchone()[0]
        regional = conn.execute(
            """
            SELECT region, ROUND(SUM(oil_production_twh), 1)
            FROM oil_production
            WHERE year = ? AND has_production = 1
            GROUP BY region ORDER BY region
            """,
            (latest_year,),
        ).fetchall()
        top = conn.execute(
            """
            SELECT country, ROUND(oil_production_twh, 1), ROUND(yoy_change_pct, 2)
            FROM oil_production
            WHERE year = ? AND has_production = 1
            ORDER BY oil_production_twh DESC LIMIT 5
            """,
            (latest_year,),
        ).fetchall()
        rents_year = conn.execute(
            "SELECT MAX(year) FROM oil_rents WHERE has_rents = 1"
        ).fetchone()[0]
        rents = conn.execute(
            """
            SELECT country, ROUND(oil_rents_pct_gdp, 2)
            FROM oil_rents WHERE year = ? AND has_rents = 1
            ORDER BY oil_rents_pct_gdp DESC
            """,
            (rents_year,),
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "Deepwater Offshore Oil Production: Africa vs USA Gulf of Mexico",
        f"Pipeline run date: {run_date}",
        "",
        "Row counts:",
        *[f"  {t:<15} {n}" for t, n in counts.items()],
        "",
        f"Production by region, {latest_year} (TWh):",
        *[f"  {r:<10} {v}" for r, v in regional],
        "",
        f"Top producers, {latest_year} (TWh, YoY %):",
        *[f"  {c:<20} {v:>9}  {y if y is not None else 'n/a'}" for c, v, y in top],
        "",
        f"Oil rents % of GDP, {rents_year}:",
        *[f"  {c:<20} {v}" for c, v in rents],
    ]
    report_path = REPORT_DIR / f"pipeline_report_{run_date}.txt"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


if AIRFLOW_AVAILABLE:
    with DAG(
        dag_id="deepwater_oil_africa_usa_pipeline",
        description="OWID + World Bank deepwater oil ETL: Africa vs USA Gulf of Mexico",
        default_args=default_args,
        schedule_interval="0 6 * * 1,3,5",
        catchup=False,
        max_active_runs=1,
        tags=["oil_gas", "africa", "usa", "deepwater", "etl"],
    ) as dag:
        ingest_task = PythonOperator(task_id="ingest_data", python_callable=run_ingestion)
        transform_task = PythonOperator(task_id="transform_data", python_callable=run_transformation)
        load_task = PythonOperator(task_id="load_data", python_callable=run_loading)
        report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

        ingest_task >> transform_task >> load_task >> report_task
else:
    dag = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    skip_ingest = "--skip-ingest" in sys.argv
    if not skip_ingest:
        run_ingestion()
    run_transformation()
    run_loading()
    print(generate_report())
