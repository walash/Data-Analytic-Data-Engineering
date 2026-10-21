# African Student Mobility to/from the USA - pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t africa-usa-student-mobility:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO \
    CONFIG_PATH=/app/config/config.yaml

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Airflow is excluded from the runtime image (it runs in its own scheduler);
# everything else is installed with pinned versions for reproducibility.
COPY requirements.txt .
RUN grep -v '^apache-airflow' requirements.txt > requirements-runtime.txt \
    && pip install -r requirements-runtime.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/
COPY notebooks/ ./notebooks/

RUN mkdir -p data/raw data/processed reports \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

VOLUME ["/app/data", "/app/reports"]

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py && sqlite3 data/student_mobility.db < sql/queries.sql > reports/query_results.txt"]
