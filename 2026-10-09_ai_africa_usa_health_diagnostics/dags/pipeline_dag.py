"""Airflow DAG: AI Healthcare Diagnostics (Africa & USA) ETL pipeline.

Orchestrates the four stages of the project on Monday, Wednesday and Friday
at 06:00 UTC:

    ingest_data -> transform_data -> load_data -> generate_report

* ingest_data      ``src/ingestion/ingest.py``: pulls 5 World Bank health
                   indicators for 14 African countries + USA into data/raw/
* transform_data   ``src/transformation/transform.py``: builds the wide,
                   cleaned table data/processed/health_diagnostics_processed.csv
* load_data        ``src/loading/load.py``: refreshes the SQLite database
                   data/health_diagnostics.db
* generate_report  writes a plain-text Africa-vs-USA gap summary to
                   data/reports/health_gap_report_<ds>.txt

The project root defaults to the parent of this ``dags/`` folder and can be
overridden with the ``HEALTH_DIAGNOSTICS_HOME`` environment variable. Every
task retries twice with a five-minute delay.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_HOME = os.getenv(
    "HEALTH_DIAGNOSTICS_HOME", str(Path(__file__).resolve().parents[1])
)

default_args = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(minutes=30),
}

REPORT_COMMAND = f"""
set -euo pipefail
cd "{PROJECT_HOME}"
mkdir -p data/reports
python3 - "{{{{ ds }}}}" <<'PYEOF'
import sqlite3
import sys

run_date = sys.argv[1]
conn = sqlite3.connect("data/health_diagnostics.db")
year = conn.execute(
    "SELECT MAX(year) FROM health_indicators "
    "WHERE africa_flag = 0 AND physicians_per_1000 IS NOT NULL "
    "AND hospital_beds_per_1000 IS NOT NULL"
).fetchone()[0]
rows = conn.execute(
    "SELECT CASE africa_flag WHEN 1 THEN 'Africa' ELSE 'USA' END, "
    "AVG(health_expenditure_pc), AVG(physicians_per_1000), AVG(under5_mortality), "
    "AVG(hospital_beds_per_1000), AVG(life_expectancy), AVG(healthcare_access_score) "
    "FROM health_indicators WHERE year = ? GROUP BY africa_flag ORDER BY africa_flag DESC",
    (year,),
).fetchall()
ranking = conn.execute(
    "SELECT country_name, healthcare_access_score FROM health_indicators "
    "WHERE year = ? ORDER BY healthcare_access_score DESC",
    (year,),
).fetchall()
conn.close()

lines = [
    "AI-Powered Healthcare Diagnostics - Africa vs USA gap report",
    f"Run date: {{run_date}} | Reference year: {{year}}",
    "",
    f"{{'Region':<8}}{{'Exp/cap $':>12}}{{'Phys/1k':>10}}{{'U5 mort':>10}}"
    f"{{'Beds/1k':>10}}{{'Life exp':>10}}{{'Access':>9}}",
]
for r in rows:
    vals = [v if v is not None else float("nan") for v in r[1:]]
    lines.append(
        f"{{r[0]:<8}}{{vals[0]:>12,.0f}}{{vals[1]:>10.2f}}{{vals[2]:>10.1f}}"
        f"{{vals[3]:>10.2f}}{{vals[4]:>10.1f}}{{vals[5]:>9.1f}}"
    )
lines += ["", "Healthcare access score ranking:"]
lines += [f"  {{i:>2}}. {{name:<20}} {{score:6.1f}}" for i, (name, score) in enumerate(ranking, 1)]
path = f"data/reports/health_gap_report_{{run_date}}.txt"
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\\n".join(lines) + "\\n")
print("\\n".join(lines))
print(f"Report written to {{path}}")
PYEOF
"""

with DAG(
    dag_id="health_diagnostics_pipeline",
    description="World Bank health indicators ETL: Africa vs USA AI diagnostics gap",
    default_args=default_args,
    start_date=datetime(2026, 10, 1),
    schedule_interval="0 6 * * 1,3,5",
    catchup=False,
    max_active_runs=1,
    tags=["health", "ai", "africa", "usa", "world-bank"],
    doc_md=__doc__,
) as dag:
    ingest_data = BashOperator(
        task_id="ingest_data",
        bash_command=f'cd "{PROJECT_HOME}" && python3 src/ingestion/ingest.py',
    )

    transform_data = BashOperator(
        task_id="transform_data",
        bash_command=f'cd "{PROJECT_HOME}" && python3 src/transformation/transform.py',
    )

    load_data = BashOperator(
        task_id="load_data",
        bash_command=f'cd "{PROJECT_HOME}" && python3 src/loading/load.py',
    )

    generate_report = BashOperator(
        task_id="generate_report",
        bash_command=REPORT_COMMAND,
    )

    ingest_data >> transform_data >> load_data >> generate_report
