"""Airflow DAG: Africa -> USA brain drain ETL pipeline.

Task flow:  ingest -> transform -> load -> generate_report

* ingest           downloads 8 World Bank indicators into data/raw/
* transform        builds tidy CSVs in data/processed/
* load             loads them into SQLite at data/brain_drain.db
* generate_report  writes reports/brain_drain_report_<ds>.md with headline metrics
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

PROJECT_ROOT = Path(os.environ.get("BRAIN_DRAIN_PROJECT_ROOT",
                                   Path(__file__).resolve().parents[1]))
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


def run_ingest(**_context) -> dict:
    import ingest

    written = ingest.ingest()
    return {name: str(path) for name, path in written.items()}


def run_transform(**_context) -> dict:
    import transform

    written = transform.transform()
    return {name: str(path) for name, path in written.items()}


def run_load(**_context) -> dict:
    import load

    return load.load()


def generate_report(ds: str | None = None, **_context) -> str:
    """Summarise the freshly loaded database into a Markdown report."""
    import load

    config = load.load_config()
    db_path = PROJECT_ROOT / config["paths"]["database"]
    reports_dir = PROJECT_ROOT / config["paths"].get("reports_dir", "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_date = ds or datetime.utcnow().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("dim_country", "fact_indicator", "country_year_metrics",
                          "brain_drain_index")
        }
        top_pressure = conn.execute(
            "SELECT brain_drain_rank, country_name, brain_drain_index, unemployment, "
            "gdp_per_capita FROM brain_drain_index ORDER BY brain_drain_rank LIMIT 5"
        ).fetchall()
        outflows = conn.execute(
            "SELECT country_name, SUM(net_migration) AS cum FROM country_year_metrics "
            "WHERE net_migration IS NOT NULL GROUP BY country_name ORDER BY cum ASC LIMIT 5"
        ).fetchall()
        remit = conn.execute(
            "SELECT country_name, remittances_usd_year, remittances_usd / 1e9 "
            "FROM brain_drain_index WHERE remittances_usd IS NOT NULL "
            "ORDER BY remittances_usd DESC LIMIT 5"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        f"# Africa -> USA Brain Drain Pipeline Report ({run_date})",
        "",
        "## Row counts",
        "",
        "| Table | Rows |",
        "|---|---|",
        *[f"| {t} | {n} |" for t, n in counts.items()],
        "",
        "## Top 5 - Brain Drain Pressure Index",
        "",
        "| Rank | Country | Index | Unemployment % | GDP per capita (US$) |",
        "|---|---|---|---|---|",
        *[f"| {r} | {c} | {i:.1f} | {u:.2f} | {g:,.0f} |" for r, c, i, u, g in top_pressure],
        "",
        "## Largest cumulative net emigration (people)",
        "",
        *[f"- {c}: {cum:,.0f}" for c, cum in outflows],
        "",
        "## Largest remittance inflows (latest year)",
        "",
        *[f"- {c} ({y}): US${bn:.2f} bn" for c, y, bn in remit],
        "",
        "Source: World Bank World Development Indicators (api.worldbank.org/v2).",
    ]
    report_path = reports_dir / f"brain_drain_report_{run_date}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
    return str(report_path)


with DAG(
    dag_id="africa_usa_brain_drain_pipeline",
    description="World Bank migration, remittance & labour-market ETL for African brain drain",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["immigration", "africa", "usa", "brain-drain", "world-bank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform", python_callable=run_transform)
    load_task = PythonOperator(task_id="load", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
