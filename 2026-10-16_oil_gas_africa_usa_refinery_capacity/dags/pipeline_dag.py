"""
Apache Airflow DAG - Oil & Gas Refinery Capacity & Fuel Access (Africa vs USA).

Task graph::

    ingest_worldbank_data >> transform_to_master >> load_to_sqlite >> generate_report

* Schedule: monthly (World Bank WDI indicators are revised a few times a year;
  a monthly run picks up revisions without hammering the public API).
* Retries: 3 attempts with exponential back-off starting at 5 minutes.
* ``catchup=False`` - the API always returns the most recent values, so
  back-filling historic logical dates adds no information.

Set the ``OIL_GAS_PROJECT_ROOT`` environment variable if the DAG file is
deployed outside the project tree (e.g. copied into ``$AIRFLOW_HOME/dags``).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(
    os.environ.get("OIL_GAS_PROJECT_ROOT", Path(__file__).resolve().parents[1])
)
for sub in ("src/ingestion", "src/transformation", "src/loading"):
    path = str(PROJECT_ROOT / sub)
    if path not in sys.path:
        sys.path.insert(0, path)

DB_PATH = PROJECT_ROOT / "data" / "oil_gas_refinery.db"
REPORTS_DIR = PROJECT_ROOT / "reports"

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email": ["data-alerts@example.com"],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=30),
}


def run_ingest(**_context) -> dict:
    """Fetch all six World Bank indicators into ``data/raw``."""
    import ingest

    ingest.setup_logging()
    summary = ingest.ingest()
    if not summary:
        raise RuntimeError("Ingestion produced no output")
    return summary


def run_transform(**_context) -> dict:
    """Clean, merge and enrich raw CSVs into ``data/processed``."""
    import transform

    transform.setup_logging()
    return transform.transform()


def run_load(**_context) -> dict:
    """Load processed data into the SQLite warehouse and validate row counts."""
    import load

    load.setup_logging()
    counts = load.load()
    if counts.get("fact_energy_indicators", 0) == 0:
        raise ValueError("fact_energy_indicators is empty after load")
    return counts


def generate_report(ds: str, **_context) -> str:
    """Write a Markdown run report with row counts and headline metrics.

    Args:
        ds: Airflow logical date (YYYY-MM-DD), used in the file name.

    Returns:
        Path of the generated report.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"pipeline_report_{ds}.md"
    with sqlite3.connect(DB_PATH) as conn:
        tables = ["dim_country", "dim_indicator", "fact_energy_indicators",
                  "country_latest_snapshot", "data_quality_report"]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        gap = conn.execute(
            """
            SELECT c.country, s.fuel_exports_pct_merchandise, s.fuel_imports_pct_merchandise,
                   s.fuel_exports_pct_merchandise_year
            FROM country_latest_snapshot s JOIN dim_country c USING (iso3)
            WHERE s.fuel_exports_pct_merchandise >= 50 AND s.fuel_imports_pct_merchandise >= 15
            ORDER BY s.fuel_imports_pct_merchandise DESC
            """
        ).fetchall()
        access = conn.execute(
            """
            SELECT c.country, s.electricity_access_pct, s.electricity_access_pct_year
            FROM country_latest_snapshot s JOIN dim_country c USING (iso3)
            ORDER BY s.electricity_access_pct ASC LIMIT 5
            """
        ).fetchall()
        quality = conn.execute(
            "SELECT indicator, non_null, null_pct, max_year FROM data_quality_report"
        ).fetchall()

    lines = [
        f"# Oil & Gas Refinery Pipeline Report - {ds}",
        "",
        "## Row counts",
        "| Table | Rows |",
        "|---|---|",
        *[f"| {t} | {n} |" for t, n in counts.items()],
        "",
        "## Countries showing a refining gap (fuel exports >= 50%, fuel imports >= 15%)",
        "| Country | Fuel exports % | Fuel imports % | Year |",
        "|---|---|---|---|",
        *[f"| {c} | {e:.1f} | {i:.1f} | {y} |" for c, e, i, y in gap],
        "",
        "## Lowest electricity access",
        "| Country | Access % | Year |",
        "|---|---|---|",
        *[f"| {c} | {a:.1f} | {y} |" for c, a, y in access],
        "",
        "## Data quality",
        "| Indicator | Non-null | Null % | Latest year |",
        "|---|---|---|---|",
        *[f"| {i} | {n} | {p} | {y} |" for i, n, p, y in quality],
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    return str(out)


with DAG(
    dag_id="oil_gas_africa_usa_refinery_capacity",
    description="World Bank -> SQLite pipeline on Africa's refining gap and fuel access vs USA",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="@monthly",
    catchup=False,
    max_active_runs=1,
    tags=["oil_gas", "africa", "usa", "worldbank", "energy"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_worldbank_data", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform_to_master", python_callable=run_transform)
    load_task = PythonOperator(task_id="load_to_sqlite", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
