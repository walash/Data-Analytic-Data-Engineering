# =============================================================================
# Oil & Gas Refinery Capacity & Fuel Access (Africa vs USA) - pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t oil-gas-refinery-pipeline:latest .
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml

# sqlite3 CLI lets you run sql/queries.sql inside the container
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install runtime dependencies only (Airflow, Jupyter and linters are not
# needed to execute the ETL and would triple the image size).
COPY requirements.txt .
RUN grep -Ev '^(apache-airflow|jupyter|ipykernel|nbformat|flake8|pytest)==' requirements.txt \
        > requirements.runtime.txt \
    && pip install -r requirements.runtime.txt

COPY config/ ./config/
COPY src/ ./src/
COPY sql/ ./sql/

RUN useradd --create-home --uid 1000 pipeline \
    && mkdir -p data/raw data/processed reports \
    && chown -R pipeline:pipeline /app
USER pipeline

VOLUME ["/app/data"]

HEALTHCHECK --interval=1m --timeout=10s --retries=3 \
    CMD python -c "import pandas, yaml, requests" || exit 1

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
