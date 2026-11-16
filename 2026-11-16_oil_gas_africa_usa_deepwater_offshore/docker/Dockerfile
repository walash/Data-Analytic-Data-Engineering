# Deepwater Offshore Oil Production: Africa vs USA Gulf of Mexico
# Build from the project root:
#   docker build -f docker/Dockerfile -t deepwater-oil-pipeline:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PROJECT_ROOT=/app \
    CONFIG_PATH=/app/config/config.yaml \
    DATA_RAW_DIR=/app/data/raw \
    DATA_PROCESSED_DIR=/app/data/processed \
    DB_PATH=/app/data/oil_production.db

WORKDIR /app

# Runtime libraries only; apache-airflow is excluded (it runs in its own
# scheduler image and is installed separately -- see requirements.txt).
COPY requirements.txt .
RUN grep -v '^apache-airflow' requirements.txt > requirements-runtime.txt \
    && pip install --upgrade pip \
    && pip install -r requirements-runtime.txt

COPY . .
RUN mkdir -p data/raw data/processed data/reports

# Full pipeline: ingest -> transform -> load -> report (the report step
# reuses the DAG callables without needing Airflow).
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py && python dags/pipeline_dag.py --skip-ingest"]
