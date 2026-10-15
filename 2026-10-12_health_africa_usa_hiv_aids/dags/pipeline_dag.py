"""Airflow DAG orchestrating the HIV/AIDS Africa & USA ETL pipeline.

Task flow::

    ingest_data >> transform_data >> load_to_db >> generate_report

Runs at 06:00 UTC every Monday, Wednesday and Friday. Each task calls the
corresponding pipeline module; ``generate_report`` writes a plain-text summary
of the loaded SQLite database to ``data/processed/pipeline_report.txt``.
"""

from __future__ import annotations

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


def _ingest() -> dict:
    """Fetch all World Bank indicators into data/raw/."""
    import ingest

    ingest.setup_logging()
    return ingest.ingest(ingest.load_config())


def _transform() -> int:
    """Clean and merge raw indicators into processed CSVs."""
    import transform

    transform.setup_logging()
    processed, _ = transform.transform(transform.load_config())
    return len(processed)


def _load() -> dict:
    """Load processed data into the SQLite database."""
    import load

    load.setup_logging()
    return load.load(load.load_config())


def _generate_report() -> str:
    """Summarise the database contents into a text report."""
    import load

    config = load.load_config()
    db_path = load.resolve_db_path(config)
    report_path = PROJECT_ROOT / config["paths"]["report_file"]
    conn = sqlite3.connect(db_path)
    try:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("countries", "indicators", "hiv_metrics")}
        latest_year = conn.execute(
            "SELECT MAX(year) FROM hiv_metrics WHERE hiv_prevalence_pct IS NOT NULL"
        ).fetchone()[0]
        top = conn.execute(
            """SELECT c.country_name, m.hiv_prevalence_pct, m.art_coverage_pct,
                      CAST(m.treatment_gap AS INTEGER)
               FROM hiv_metrics m JOIN countries c USING (country_code)
               WHERE m.year = ? AND m.hiv_prevalence_pct IS NOT NULL
               ORDER BY m.hiv_prevalence_pct DESC LIMIT 5""",
            (latest_year,),
        ).fetchall()
        on_target = conn.execute(
            "SELECT COUNT(*) FROM hiv_metrics WHERE year = ? AND meets_90_90_90_art = 1",
            (latest_year,),
        ).fetchone()[0]
    finally:
        conn.close()

    lines = [
        "HIV/AIDS Africa & USA pipeline report",
        f"Generated: {datetime.utcnow():%Y-%m-%d %H:%M} UTC",
        "",
        "Row counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()),
        f"Latest HIV data year: {latest_year}",
        f"Countries at/above 90-90-90 ART level (>=81%): {on_target}",
        "",
        "Top 5 by HIV prevalence (country | prevalence % | ART % | untreated PLHIV):",
    ]
    lines += [f"  {name} | {prev} | {art} | {gap:,}" for name, prev, art, gap in top]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    dag_id="health_africa_usa_hiv_aids_pipeline",
    description="World Bank HIV/AIDS indicators for Africa & USA -> SQLite",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["health", "hiv", "africa", "usa", "world-bank"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=_ingest)
    transform_data = PythonOperator(task_id="transform_data", python_callable=_transform)
    load_to_db = PythonOperator(task_id="load_to_db", python_callable=_load)
    generate_report = PythonOperator(task_id="generate_report",
                                     python_callable=_generate_report)

    ingest_data >> transform_data >> load_to_db >> generate_report
