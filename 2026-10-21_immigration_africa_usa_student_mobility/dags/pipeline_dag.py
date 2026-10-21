"""Airflow DAG: African Student Mobility to/from the USA.

Flow: ingest_worldbank -> transform_indicators -> load_sqlite -> generate_report

Each task calls the public entry point of the matching pipeline module so the
same code runs in Airflow, Docker and locally.  ``generate_report`` writes a
Markdown summary (row counts + Student Mobility Pressure ranking + USA
benchmarks) to ``reports/pipeline_report.md``.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def run_ingest(**_context) -> dict:
    from ingest import ingest

    return ingest()


def run_transform(**_context) -> dict:
    from transform import transform

    return transform()


def run_load(**_context) -> dict:
    from load import load

    return load()


def generate_report(**context) -> str:
    """Build a Markdown run report from the SQLite database."""
    from load import get_paths, load_config

    cfg = load_config()
    _, db_path = get_paths(cfg)
    report_path = PROJECT_ROOT / cfg["pipeline"]["report_file"]
    report_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        tables = ["countries", "indicators", "indicator_observations",
                  "country_year_metrics", "mobility_snapshot"]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        ranking = conn.execute(
            """
            SELECT pressure_rank, country_name, sub_region,
                   ROUND(mobility_pressure_index, 1), mobility_pressure_index_year,
                   ROUND(tertiary_enrollment, 1), tertiary_enrollment_year,
                   ROUND(gdp_pc_pct_of_usa, 2)
            FROM mobility_snapshot
            WHERE mobility_pressure_index IS NOT NULL
            ORDER BY pressure_rank
            """
        ).fetchall()
        usa = conn.execute(
            """
            SELECT
              (SELECT year || ': ' || ROUND(tertiary_enrollment, 1) FROM country_year_metrics
               WHERE iso3 = 'USA' AND tertiary_enrollment IS NOT NULL
               ORDER BY year DESC LIMIT 1),
              (SELECT year || ': ' || ROUND(gdp_per_capita, 0) FROM country_year_metrics
               WHERE iso3 = 'USA' AND gdp_per_capita IS NOT NULL
               ORDER BY year DESC LIMIT 1),
              (SELECT year || ': ' || CAST(net_migration AS INTEGER) FROM country_year_metrics
               WHERE iso3 = 'USA' AND net_migration IS NOT NULL
               ORDER BY year DESC LIMIT 1)
            """
        ).fetchone()
    finally:
        conn.close()

    run_id = context.get("run_id", "manual")
    lines = [
        "# African Student Mobility Pipeline Report",
        "",
        f"- Generated (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Airflow run id: {run_id}",
        f"- Database: `{Path(db_path).name}`",
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "|---|---:|",
        *[f"| {t} | {n:,} |" for t, n in counts.items()],
        "",
        "## Student Mobility Pressure Index (latest)",
        "",
        "| Rank | Country | Sub-region | Index | Year | Tertiary enrol. % | Enrol. year "
        "| GDP pc % of USA |",
        "|---:|---|---|---:|---:|---:|---:|---:|",
        *[f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} |"
          for r in ranking],
        "",
        "## USA benchmarks (latest year: value)",
        "",
        f"- Tertiary enrollment (% gross): {usa[0]}",
        f"- GDP per capita (US$): {usa[1]}",
        f"- Net migration (persons): {usa[2]}",
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="africa_usa_student_mobility_pipeline",
    description="World Bank education/migration indicators: Africa (15) vs USA",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["immigration", "education", "africa", "usa", "world-bank"],
    doc_md=__doc__,
) as dag:
    ingest_task = PythonOperator(task_id="ingest_worldbank", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform_indicators", python_callable=run_transform)
    load_task = PythonOperator(task_id="load_sqlite", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
