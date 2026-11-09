"""Airflow DAG orchestrating the African Asylum Seekers & Immigration Flows to the USA pipeline.

Task graph (weekly)::

    ingest_data >> transform_data >> load_data >> generate_report

Each task imports and runs the corresponding pipeline module from ``src/``. The
``generate_report`` task writes a plain-text summary of the SQLite warehouse to
``data/pipeline_report.txt`` and pushes the headline metrics to XCom.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 11, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def run_ingestion(**_context) -> int:
    """Download World Bank indicators to data/raw/."""
    import ingest

    files = ingest.ingest(dry_run=False)
    return len(files)


def run_transformation(**_context) -> list:
    """Clean and enrich raw data into data/processed/."""
    import transform

    return [str(p) for p in transform.transform().values()]


def run_loading(**_context) -> str:
    """Load processed CSVs into the SQLite warehouse."""
    import load

    return str(load.load())


def generate_report(**context) -> dict:
    """Summarise the warehouse and write data/pipeline_report.txt."""
    import load

    config = load.load_config()
    _, db_path = load.get_paths(config)
    report_path = PROJECT_ROOT / config["paths"]["report_path"]

    with sqlite3.connect(db_path) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("migration_flows", "remittance_flows", "country_profiles")}
        latest_year = conn.execute("SELECT MAX(year) FROM migration_flows").fetchone()[0]
        africa_net, usa_net = conn.execute(
            """SELECT SUM(CASE WHEN region = 'Africa' THEN net_migration END),
                      SUM(CASE WHEN region = 'USA' THEN net_migration END)
               FROM migration_flows WHERE year = ?""", (latest_year,)).fetchone()
        top_outflows = conn.execute(
            """SELECT country_name, SUM(net_migration) AS total
               FROM migration_flows WHERE region = 'Africa'
               GROUP BY country_name ORDER BY total ASC LIMIT 5""").fetchall()

    lines = [
        "African Asylum Seekers & Immigration Flows to the USA - pipeline report",
        f"Run date (logical): {context.get('ds', datetime.utcnow().date().isoformat())}",
        "",
        "Row counts:",
        *[f"  {table}: {count}" for table, count in counts.items()],
        "",
        f"Latest year: {latest_year}",
        f"  Africa aggregate net migration: {africa_net:,.0f}",
        f"  USA net migration:              {usa_net:,.0f}",
        "",
        "Top 5 African net-emigration countries (cumulative):",
        *[f"  {name}: {total:,.0f}" for name, total in top_outflows],
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report_path": str(report_path), "latest_year": latest_year, **counts}


with DAG(
    dag_id="africa_usa_asylum_immigration_pipeline",
    description="Weekly ETL of World Bank migration & remittance data for Africa and the USA",
    default_args=default_args,
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["immigration", "africa", "usa", "asylum", "worldbank"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=run_ingestion)
    transform_data = PythonOperator(task_id="transform_data", python_callable=run_transformation)
    load_data = PythonOperator(task_id="load_data", python_callable=run_loading)
    report = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_data >> transform_data >> load_data >> report
