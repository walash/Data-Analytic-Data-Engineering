"""Airflow DAG orchestrating the Africa-USA Road Infrastructure & Trade Corridors pipeline.

Weekly run:  ingest_data -> transform_data -> load_to_db -> generate_report

Each task imports and calls the ``main``/stage function of the matching
module in ``src/``. The report task queries the SQLite warehouse and writes
a plain-text summary to ``data/processed/pipeline_report.txt``.
"""

import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

DB_PATH = PROJECT_ROOT / "data" / "transport_trade.db"
REPORT_PATH = PROJECT_ROOT / "data" / "processed" / "pipeline_report.txt"

logger = logging.getLogger(__name__)

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "start_date": datetime(2026, 10, 5),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}


def run_ingestion(**_context):
    """Fetch all seven World Bank indicators into data/raw/.

    Raises:
        RuntimeError: If any indicator could not be downloaded.
    """
    from ingestion.ingest import ingest

    summary = ingest(dry_run=False)
    failed = [k for k, v in summary.items() if v < 0]
    if failed:
        raise RuntimeError(f"Ingestion failed for: {failed}")
    return summary


def run_transformation(**_context):
    """Clean, merge and enrich raw CSVs into data/processed/.

    Returns:
        dict with row counts of the master and summary outputs.
    """
    from transformation.transform import transform

    master, summary = transform()
    return {"master_rows": len(master), "summary_rows": len(summary)}


def run_loading(**_context):
    """Load processed data into the SQLite warehouse.

    Returns:
        dict mapping table name to rows loaded.
    """
    from loading.load import load

    return load()


def generate_report(**_context):
    """Write a text report with table counts and the LPI leaderboard.

    Returns:
        Path of the written report as a string.
    """
    lines = [
        "Africa-USA Road Infrastructure & Trade Corridors - Pipeline Report",
        f"Generated: {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC",
        "",
        "Table row counts:",
    ]
    with sqlite3.connect(DB_PATH) as conn:
        for table in ("raw_indicators", "master_transport_trade", "country_summary", "lpi_rankings"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            lines.append(f"  {table:<24} {count:>6}")
        lines += ["", "Latest LPI vs USA (country, year, score, gap):"]
        rows = conn.execute(
            "SELECT country_name, lpi_overall_year, lpi_overall, lpi_gap_vs_usa "
            "FROM country_summary ORDER BY lpi_rank"
        ).fetchall()
        for name, year, score, gap in rows:
            lines.append(f"  {name:<20} {year}  {score:.2f}  {gap:+.2f}")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Report written to %s", REPORT_PATH)
    return str(REPORT_PATH)


with DAG(
    dag_id="africa_usa_road_trade_corridors_pipeline",
    description="Weekly ETL of World Bank road infrastructure, LPI and trade indicators for Africa & USA",
    default_args=default_args,
    schedule_interval="@weekly",
    catchup=False,
    max_active_runs=1,
    tags=["transportation", "africa", "usa", "world-bank", "trade-corridors"],
) as dag:
    ingest_data = PythonOperator(task_id="ingest_data", python_callable=run_ingestion)
    transform_data = PythonOperator(task_id="transform_data", python_callable=run_transformation)
    load_to_db = PythonOperator(task_id="load_to_db", python_callable=run_loading)
    report = PythonOperator(task_id="generate_report", python_callable=generate_report)

    ingest_data >> transform_data >> load_to_db >> report
