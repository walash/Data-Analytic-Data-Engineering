"""Airflow DAG for the Maternal & Child Health (Africa vs USA) pipeline.

Task graph (weekly):

    ingest_data >> transform_data >> load_to_db >> generate_report

* ingest_data     - fetch 7 World Bank indicators to data/raw/
* transform_data  - clean, pivot and enrich into data/processed/
* load_to_db      - load processed tables into SQLite
* generate_report - write a Markdown summary of key metrics to data/reports/

Set the Airflow Variable or env var ``MCH_PROJECT_ROOT`` if the project is not
located one directory above this DAG file.
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

PROJECT_ROOT = Path(os.environ.get("MCH_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

logger = logging.getLogger(__name__)

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}


def _ingest(**_context) -> dict:
    """Fetch all configured World Bank indicators."""
    import ingest

    config = ingest.load_config()
    return ingest.ingest(config)


def _transform(**_context) -> dict:
    """Transform raw CSVs into processed outputs."""
    import transform

    outputs = transform.transform(transform.load_config())
    return {name: str(path) for name, path in outputs.items()}


def _load(**_context) -> dict:
    """Load processed tables into the SQLite database."""
    import load

    return load.load(load.load_config())


def _generate_report(**context) -> str:
    """Query the database and write a Markdown report of headline metrics."""
    import load

    config = load.load_config()
    db_path = load.get_paths(config)["db"]
    reports_dir = PROJECT_ROOT / config["paths"].get("reports_dir", "data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = context.get("ds") or datetime.utcnow().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in load.LOAD_ORDER
        }
        latest_year = conn.execute(
            "SELECT MAX(year) FROM yearly_summary WHERE avg_maternal_mortality_ratio IS NOT NULL"
        ).fetchone()[0]
        region_rows = conn.execute(
            "SELECT region, avg_maternal_mortality_ratio, avg_under5_mortality_rate, "
            "avg_neonatal_mortality_rate, avg_health_expenditure_per_capita_usd "
            "FROM yearly_summary WHERE year = ? ORDER BY region",
            (latest_year,),
        ).fetchall()
        top_mmr = conn.execute(
            "SELECT country_name, maternal_mortality_ratio FROM maternal_child_health_metrics "
            "WHERE year = ? AND maternal_mortality_ratio IS NOT NULL "
            "ORDER BY maternal_mortality_ratio DESC LIMIT 5",
            (latest_year,),
        ).fetchall()
        sdg = conn.execute(
            "SELECT country_name FROM maternal_child_health_metrics "
            "WHERE year = ? AND meets_sdg_mmr = 1 ORDER BY country_name",
            (latest_year,),
        ).fetchall()
    finally:
        conn.close()

    lines = [
        f"# Maternal & Child Health Pipeline Report ({run_date})",
        "",
        "## Table row counts",
        *[f"- `{t}`: {n}" for t, n in counts.items()],
        "",
        f"## Regional averages ({latest_year})",
        "| Region | MMR | U5MR | NMR | Health spend (US$) |",
        "|---|---|---|---|---|",
        *[
            f"| {r[0]} | {r[1]:.1f} | {r[2]:.1f} | {r[3]:.1f} | {r[4]:.0f} |"
            for r in region_rows
            if None not in r
        ],
        "",
        f"## Highest maternal mortality ({latest_year})",
        *[f"{i}. {name}: {value:.0f} per 100,000 live births" for i, (name, value) in enumerate(top_mmr, 1)],
        "",
        "## Meeting SDG 3.1 (MMR < 70)",
        ", ".join(row[0] for row in sdg) or "None",
        "",
    ]
    report_path = reports_dir / f"pipeline_report_{run_date}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


with DAG(
    dag_id="maternal_child_health_africa_usa_pipeline",
    description="Weekly ETL of World Bank maternal & child health indicators (Africa vs USA)",
    default_args=default_args,
    start_date=datetime(2026, 9, 30),
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["health", "maternal-child-health", "world-bank", "africa", "usa"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=_ingest)
    transform_data = PythonOperator(task_id="transform_data", python_callable=_transform)
    load_to_db = PythonOperator(task_id="load_to_db", python_callable=_load)
    generate_report = PythonOperator(task_id="generate_report", python_callable=_generate_report)

    ingest_data >> transform_data >> load_to_db >> generate_report
