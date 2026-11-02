"""Apache Airflow DAG orchestrating the Urban Transit & BRT Africa-USA pipeline.

Weekly run: ingest_data -> transform_data -> load_data -> generate_report.
Each task is a PythonOperator calling the corresponding module entry point
in ``src/``. ``generate_report`` queries the SQLite database and writes a
Markdown summary to ``reports/pipeline_report.md``.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.getenv("TRANSIT_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 11, 2),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def ingest_task(**_context):
    """Run the ingestion stage and return written file paths as strings."""
    from ingestion.ingest import run_ingestion, setup_logging, load_config

    config = load_config()
    setup_logging(config)
    return {name: str(path) for name, path in run_ingestion(config).items()}


def transform_task(**_context):
    """Run the transformation stage and return processed file paths."""
    from transformation.transform import run_transformation

    return {name: str(path) for name, path in run_transformation().items()}


def load_task(**_context):
    """Run the loading stage and return per-table row counts."""
    from loading.load import run_loading

    return run_loading()


def generate_report(**_context):
    """Query the analytics DB and write a Markdown pipeline report.

    Returns:
        str: Path of the generated report.
    """
    from loading.load import load_config, resolve_paths

    _, db_path = resolve_paths(load_config())
    report_dir = PROJECT_ROOT / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "pipeline_report.md"

    conn = sqlite3.connect(str(db_path))
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("brt_systems", "urban_indicators", "brt_enriched")}
        top = conn.execute(
            "SELECT system_name, city, continent, daily_ridership FROM brt_systems "
            "ORDER BY daily_ridership DESC LIMIT 5").fetchall()
        by_continent = conn.execute(
            "SELECT continent, COUNT(*), SUM(daily_ridership), "
            "ROUND(SUM(daily_ridership) / SUM(corridor_length_km), 1), "
            "ROUND(AVG(fleet_utilization_ratio), 1) "
            "FROM brt_enriched GROUP BY continent").fetchall()
    finally:
        conn.close()

    lines = [
        "# Urban Transit & BRT Pipeline Report",
        f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Table row counts",
        *[f"- {table}: {count}" for table, count in counts.items()],
        "",
        "## Top 5 systems by daily ridership",
        *[f"{i}. {name} ({city}, {cont}): {int(rides):,}/day"
          for i, (name, city, cont, rides) in enumerate(top, 1)],
        "",
        "## Continent summary",
        "| Continent | Systems | Daily riders | Riders per km | Riders per vehicle |",
        "|---|---|---|---|---|",
        *[f"| {c} | {n} | {int(r):,} | {rpk} | {rpv} |" for c, n, r, rpk, rpv in by_continent],
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(report_path)


with DAG(
    dag_id="urban_transit_brt_pipeline",
    description="Weekly ETL for Africa-USA BRT systems and World Bank urban indicators",
    default_args=default_args,
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["transportation", "brt", "africa", "usa"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=ingest_task)
    transform_data = PythonOperator(task_id="transform_data", python_callable=transform_task)
    load_data = PythonOperator(task_id="load_data", python_callable=load_task)
    report = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_data >> transform_data >> load_data >> report
