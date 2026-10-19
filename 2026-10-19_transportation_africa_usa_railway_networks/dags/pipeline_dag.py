"""Airflow DAG orchestrating the Africa & USA Railway Networks pipeline.

Task graph (weekly)::

    ingest_data >> transform_data >> load_to_db >> generate_report

Each task calls the ``main()`` entry point of the corresponding pipeline
module, so the DAG and the CLI / Docker runs execute exactly the same code.
``generate_report`` queries the SQLite warehouse and writes a Markdown
summary to ``reports/pipeline_report.md``.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(os.environ.get("RAILWAY_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def _run(module_name: str) -> None:
    """Import a pipeline module and run its ``main()``; fail the task on non-zero exit."""
    module = __import__(module_name)
    exit_code = module.main([])
    if exit_code != 0:
        raise RuntimeError(f"{module_name}.main() exited with code {exit_code}")


def ingest_data() -> None:
    """Fetch the five World Bank rail / economic indicators into data/raw/."""
    _run("ingest")


def transform_data() -> None:
    """Clean, merge and enrich raw data into data/processed/."""
    _run("transform")


def load_to_db() -> None:
    """Load processed data into the SQLite warehouse."""
    _run("load")


def generate_report(**context) -> str:
    """Summarise table sizes and headline rail metrics into reports/pipeline_report.md."""
    import load  # noqa: E402  (module path is added at import time above)

    config = load.load_config()
    db_path = load.resolve_db_path(config)
    report_dir = PROJECT_ROOT / config["paths"]["report_dir"]
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "pipeline_report.md"

    with sqlite3.connect(db_path) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in config["database"]["tables"]}
        top = conn.execute(
            "SELECT country_name, latest_rail_lines_km, latest_rail_lines_year "
            "FROM country_profiles WHERE is_africa = 1 AND has_rail_network = 1 "
            "ORDER BY latest_rail_lines_km DESC LIMIT 5").fetchall()
        regions = conn.execute(
            "SELECT region, ROUND(SUM(latest_rail_lines_km), 0), "
            "ROUND(SUM(latest_rail_freight_million_tkm), 0), "
            "ROUND(AVG(latest_lpi_infrastructure_score), 2) "
            "FROM country_profiles GROUP BY region ORDER BY region").fetchall()

    run_date = context.get("ds", datetime.utcnow().strftime("%Y-%m-%d"))
    lines = [
        "# Africa & USA Railway Networks — Pipeline Report",
        "",
        f"Run date: {run_date}",
        "",
        "## Table row counts",
        "",
        "| Table | Rows |",
        "|---|---|",
    ]
    lines += [f"| {t} | {n} |" for t, n in counts.items()]
    lines += ["", "## Largest African rail networks (latest route-km)", "",
              "| Country | Rail lines (km) | Year |", "|---|---|---|"]
    lines += [f"| {c} | {km:,.0f} | {y} |" for c, km, y in top]
    lines += ["", "## Regional totals (latest values)", "",
              "| Region | Rail lines (km) | Freight (million ton-km) | Avg LPI infra |",
              "|---|---|---|---|"]
    lines += [f"| {r} | {km or 0:,.0f} | {fr or 0:,.0f} | {lpi if lpi is not None else 'n/a'} |"
              for r, km, fr, lpi in regions]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(report_path)


with DAG(
    dag_id="africa_usa_railway_networks_pipeline",
    description="World Bank rail infrastructure ETL: Africa vs USA",
    default_args=DEFAULT_ARGS,
    schedule_interval="@weekly",
    start_date=datetime(2026, 10, 19),
    catchup=False,
    max_active_runs=1,
    tags=["transportation", "railway", "africa", "usa", "world-bank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data", python_callable=ingest_data)
    transform_task = PythonOperator(task_id="transform_data", python_callable=transform_data)
    load_task = PythonOperator(task_id="load_to_db", python_callable=load_to_db)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
