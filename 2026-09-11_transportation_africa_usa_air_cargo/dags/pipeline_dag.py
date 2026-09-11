"""Apache Airflow DAG for the Africa-USA Air Cargo pipeline.

The DAG orchestrates the end-to-end ETL workflow on a weekly schedule:

    ingest_data -> transform_data -> load_data -> generate_report

Each stage is wrapped in a :class:`~airflow.operators.python.PythonOperator`
that imports and calls the relevant module function. The project ``src``
directory is added to ``sys.path`` so the modules resolve regardless of the
Airflow working directory.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.python import PythonOperator

# --------------------------------------------------------------------------- #
# Make the project source importable by Airflow workers.
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (str(PROJECT_ROOT), str(SRC_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _run_ingest(**_context) -> dict:
    """Airflow task callable that runs the ingestion stage.

    Returns:
        The per-source success mapping from :func:`ingestion.ingest.ingest`.
    """
    from ingestion.ingest import ingest

    return ingest()


def _run_transform(**_context) -> dict:
    """Airflow task callable that runs the transformation stage.

    Returns:
        The row-count summary from :func:`transformation.transform.transform`.
    """
    from transformation.transform import transform

    return transform()


def _run_load(**_context) -> dict:
    """Airflow task callable that runs the load stage.

    Returns:
        The per-table row-count summary from :func:`loading.load.load`.
    """
    from loading.load import load

    return load()


def _generate_report(**_context) -> str:
    """Produce a small text summary report from the loaded database.

    Reads row counts from the SQLite database and writes a report file to
    ``data/pipeline_report.txt``.

    Returns:
        The path to the generated report file.
    """
    db_path = PROJECT_ROOT / "data" / "air_cargo.db"
    report_path = PROJECT_ROOT / "data" / "pipeline_report.txt"

    lines = [
        "Africa-USA Air Cargo Pipeline Report",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "-" * 40,
    ]
    if db_path.exists():
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            for table in (
                "airports",
                "routes",
                "air_freight_stats",
                "air_passenger_stats",
            ):
                try:
                    count = cursor.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                except sqlite3.Error:
                    count = "n/a"
                lines.append(f"{table}: {count} rows")
        finally:
            conn.close()
    else:
        lines.append("Database not found; run the full pipeline first.")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return str(report_path)


default_args = {
    "owner": "data-engineering",
    "start_date": datetime(2026, 1, 1),
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="africa_usa_air_cargo_pipeline",
    description="Weekly ETL for Africa-USA air cargo & freight analysis.",
    default_args=default_args,
    schedule_interval="@weekly",
    catchup=False,
    tags=["transportation", "air-cargo", "africa", "usa"],
) as dag:

    ingest_data = PythonOperator(
        task_id="ingest_data",
        python_callable=_run_ingest,
    )

    transform_data = PythonOperator(
        task_id="transform_data",
        python_callable=_run_transform,
    )

    load_data = PythonOperator(
        task_id="load_data",
        python_callable=_run_load,
    )

    generate_report = PythonOperator(
        task_id="generate_report",
        python_callable=_generate_report,
    )

    ingest_data >> transform_data >> load_data >> generate_report
