"""Apache Airflow DAG for the AI Adoption & Digital Infrastructure pipeline.

Orchestrates the weekly end-to-end ETL workflow for the digital-infrastructure
analysis covering 43 African countries and the USA:

    ingest_data -> transform_data -> load_data -> generate_report

Each task is a ``PythonOperator`` that calls the ``main``/entry function of the
corresponding module in ``src/``. The final ``generate_report`` task queries the
freshly-loaded SQLite database and writes a small text summary of row counts and
headline metrics so a pipeline run leaves an auditable artifact.

The project ``src`` directory is added to ``sys.path`` at parse time so the
modules resolve regardless of where the Airflow scheduler runs.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# ---------------------------------------------------------------------------
# Make the project modules importable from within Airflow workers.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ingestion.ingest import ingest  # noqa: E402
from src.loading.load import load  # noqa: E402
from src.transformation.transform import transform  # noqa: E402


def _run_ingest(**_context) -> dict:
    """Airflow callable: fetch raw indicators from the World Bank API."""
    return ingest()


def _run_transform(**_context) -> dict:
    """Airflow callable: merge and enrich the raw CSVs into processed CSVs."""
    return transform()


def _run_load(**_context) -> dict:
    """Airflow callable: load processed CSVs into the SQLite database."""
    return load()


def _generate_report(**_context) -> str:
    """Airflow callable: write a text summary of the loaded database.

    Returns
    -------
    str
        Path to the generated report file.
    """
    db_path = PROJECT_ROOT / "data" / "digital_infrastructure.db"
    report_dir = PROJECT_ROOT / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "pipeline_report.txt"

    conn = sqlite3.connect(str(db_path))
    try:
        metrics_rows = conn.execute("SELECT COUNT(*) FROM digital_metrics").fetchone()[0]
        summary_rows = conn.execute("SELECT COUNT(*) FROM country_summary").fetchone()[0]
        top = conn.execute(
            """
            SELECT country_name, ROUND(internet_users_pct, 1)
            FROM country_summary
            WHERE region <> 'USA'
            ORDER BY internet_users_pct DESC
            LIMIT 5
            """
        ).fetchall()
        usa = conn.execute(
            "SELECT ROUND(internet_users_pct, 1) FROM country_summary "
            "WHERE country_code = 'USA'"
        ).fetchone()
    finally:
        conn.close()

    lines = [
        "AI Adoption & Digital Infrastructure - Pipeline Report",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "=" * 60,
        f"digital_metrics rows : {metrics_rows}",
        f"country_summary rows : {summary_rows}",
        f"USA internet penetration (latest): {usa[0] if usa else 'n/a'}%",
        "",
        "Top 5 African countries by internet penetration:",
    ]
    lines += [f"  {i}. {name}: {pct}%" for i, (name, pct) in enumerate(top, start=1)]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="ai_digital_infrastructure_pipeline",
    description="Weekly ETL for AI adoption & digital infrastructure (Africa vs USA)",
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["ai", "digital-infrastructure", "africa", "usa", "worldbank"],
) as dag:

    ingest_data = PythonOperator(
        task_id="ingest_data",
        python_callable=_run_ingest,
    )

    transform_data = PythonOperator(
        task_id="transform_data",
        python_callable=_run_transform,
    )

    load_data = PythonOperator(
        task_id="load_data",
        python_callable=_run_load,
    )

    generate_report = PythonOperator(
        task_id="generate_report",
        python_callable=_generate_report,
    )

    ingest_data >> transform_data >> load_data >> generate_report
