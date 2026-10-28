# Tuberculosis Burden (Africa & USA) - ETL pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t tb-burden-pipeline:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml \
    LOG_LEVEL=INFO

WORKDIR /app

# sqlite3 CLI lets users run sql/queries.sql inside the container
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install only the runtime dependencies (Airflow/Jupyter are not needed in the pipeline image)
COPY requirements.txt .
RUN grep -Ev '^(apache-airflow|jupyter|notebook|nbconvert|ipykernel|flake8)' requirements.txt \
        > requirements-runtime.txt \
    && pip install -r requirements-runtime.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

RUN mkdir -p data/raw data/processed data/reports db \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

VOLUME ["/app/data", "/app/db"]

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
