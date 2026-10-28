"""
Apache Airflow DAG for the Africa & USA Tuberculosis Burden pipeline.

Task graph::

    ingest_data >> transform_data >> load_data >> generate_report

Runs at 06:00 UTC every Monday, Wednesday and Friday. Each task calls the
``main``-level function of the corresponding pipeline module so the exact
same code path is used by the CLI, Docker and Airflow.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=30),
    "execution_timeout": timedelta(minutes=30),
}


def run_ingest(**context) -> dict:
    """Fetch WHO GHO + World Bank data into data/raw/."""
    import ingest

    counts = ingest.ingest(ingest.load_config())
    context["ti"].xcom_push(key="ingest_counts", value=counts)
    return counts


def run_transform(**context) -> dict:
    """Clean, enrich and merge raw data into data/processed/."""
    import transform

    counts = transform.transform(transform.load_config())
    context["ti"].xcom_push(key="transform_counts", value=counts)
    return counts


def run_load(**context) -> dict:
    """Load processed CSVs into the SQLite warehouse."""
    import load

    counts = load.load(load.load_config())
    context["ti"].xcom_push(key="load_counts", value=counts)
    return counts


def generate_report(**context) -> str:
    """Write a plain-text run report with table counts and headline metrics."""
    import load

    config = load.load_config()
    db_path = load.get_paths(config)["db"]
    reports_dir = PROJECT_ROOT / config["paths"].get("reports_dir", "data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
        headline = conn.execute(
            """WITH latest_treatment AS (
                   SELECT country_code, tb_treatment_success_pct,
                          ROW_NUMBER() OVER (PARTITION BY country_code
                                             ORDER BY year DESC) AS rn
                   FROM fact_tb_treatment)
               SELECT v.region, v.year, ROUND(AVG(v.tb_incidence_per_100k), 1),
                      ROUND(AVG(v.tb_mortality_per_100k), 2),
                      ROUND(AVG(t.tb_treatment_success_pct), 1)
               FROM v_latest_country_burden v
               LEFT JOIN latest_treatment t
                      ON t.country_code = v.country_code AND t.rn = 1
               GROUP BY v.region, v.year
               ORDER BY v.region""").fetchall()
        top5 = conn.execute(
            """SELECT country_name, tb_incidence_per_100k FROM v_latest_country_burden
               WHERE region = 'Africa' ORDER BY tb_incidence_per_100k DESC LIMIT 5""").fetchall()
    finally:
        conn.close()

    run_ts = context.get("ts", datetime.utcnow().isoformat())
    lines = [
        "TB Burden Pipeline - Run Report",
        f"Run timestamp : {run_ts}",
        f"Database      : {db_path}",
        "",
        "Table row counts:",
        *[f"  {t:<26}{n:>8,}" for t, n in counts.items()],
        "",
        "Latest-year headline (region, year, incidence/100k, mortality/100k, "
        "latest treatment success %):",
        *[f"  {r[0]:<7}{r[1]:<6}{r[2]!s:>8}{r[3]!s:>8}{r[4]!s:>8}" for r in headline],
        "",
        "Top 5 African countries by latest incidence per 100k:",
        *[f"  {name:<28}{rate:>8.1f}" for name, rate in top5],
    ]
    report = "\n".join(lines)
    run_date = context.get("ds_nodash") or datetime.utcnow().strftime("%Y%m%d")
    out = reports_dir / f"run_report_{run_date}.txt"
    out.write_text(report, encoding="utf-8")
    logger.info("Report written to %s\n%s", out, report)
    return str(out)


with DAG(
    dag_id="africa_usa_tuberculosis_burden_pipeline",
    description="WHO GHO + World Bank TB burden ETL for Africa and the USA",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["health", "tuberculosis", "africa", "usa", "who", "worldbank"],
) as dag:
    ingest_task = PythonOperator(task_id="ingest_data", python_callable=run_ingest)
    transform_task = PythonOperator(task_id="transform_data", python_callable=run_transform)
    load_task = PythonOperator(task_id="load_data", python_callable=run_load)
    report_task = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_task >> transform_task >> load_task >> report_task
