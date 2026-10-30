"""Airflow DAG: Natural Gas Pipelines — Africa & USA ETL.

Task graph:
    ingest_data >> transform_data >> load_data >> generate_report

Each ETL task calls the ``ingest`` / ``transform`` / ``load`` function of the
matching module in ``src/``. ``generate_report`` queries the SQLite warehouse
and writes a Markdown summary to ``reports/pipeline_report_<ds>.md``.

Set the ``NGAS_PROJECT_ROOT`` environment variable if the project is not located one directory above this DAG file.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from airflow import DAG
from airflow.operators.python import PythonOperator

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.environ.get("NGAS_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def _config() -> Dict[str, Any]:
    import ingest

    return ingest.load_config(PROJECT_ROOT / "config" / "config.yaml")


def run_ingest(**_: Any) -> Dict[str, int]:
    import ingest

    results = ingest.ingest(_config())
    logger.info("Ingest results: %s", results)
    return results


def run_transform(**_: Any) -> Dict[str, int]:
    import transform

    counts = transform.transform(_config())
    if counts.get("gas_indicators_long", 0) == 0:
        raise ValueError("Transformation produced no indicator rows")
    return counts


def run_load(**_: Any) -> Dict[str, int]:
    import load

    return load.load(_config())


def generate_report(ds: str, **_: Any) -> str:
    """Write a Markdown report with table counts and headline rankings."""
    config = _config()
    db_path = PROJECT_ROOT / config["paths"]["database"]
    report_dir = PROJECT_ROOT / config["paths"].get("report_dir", "reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    latest_sql = """
        WITH latest AS (
            SELECT iso3, MAX(year) AS year FROM gas_indicators
            WHERE indicator_key = ? GROUP BY iso3
        )
        SELECT c.country_name, g.year, ROUND(g.value, 2)
        FROM gas_indicators g
        JOIN latest l ON l.iso3 = g.iso3 AND l.year = g.year
        JOIN countries c ON c.iso3 = g.iso3
        WHERE g.indicator_key = ? AND c.is_focus = 1
        ORDER BY g.value DESC
    """
    lines = [f"# Natural Gas Pipelines — Africa & USA pipeline report ({ds})", ""]
    with sqlite3.connect(db_path) as conn:
        lines += ["## Table row counts", "", "| Table | Rows |", "|---|---|"]
        for table in ("countries", "gas_indicators", "country_year_panel", "pipeline_infrastructure"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            lines.append(f"| {table} | {n} |")

        for key, spec in config["worldbank"]["indicators"].items():
            rows = conn.execute(latest_sql, (key, key)).fetchall()
            lines += ["", f"## {spec['name']}", "", "| Country | Year | Value |", "|---|---|---|"]
            lines += [f"| {name} | {year} | {value} |" for name, year, value in rows]

        cap = conn.execute(
            "SELECT ROUND(SUM(capacity_bcm_y), 1), COUNT(*) FROM pipeline_infrastructure "
            "WHERE origin_iso3 <> 'USA'"
        ).fetchone()
        lines += ["", "## Pipeline infrastructure", "",
                  f"{cap[1]} African pipelines catalogued, ~{cap[0]} bcm/y combined nameplate capacity."]

    report_path = report_dir / f"pipeline_report_{ds}.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", report_path)
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
    dag_id="africa_usa_natural_gas_pipelines",
    description="World Bank natural gas indicators + pipeline catalogue -> SQLite -> report",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["oil_gas", "natural_gas", "africa", "usa", "world_bank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform_data", python_callable=run_transform)
    load_task = PythonOperator(task_id="load_data", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
