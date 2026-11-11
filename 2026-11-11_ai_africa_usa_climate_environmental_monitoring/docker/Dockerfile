# Container image for the AI-Driven Climate & Environmental Monitoring pipeline
# Build from the project root:
#   docker build -f docker/Dockerfile -t climate-monitoring:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
# apache-airflow is excluded: the container runs the ETL scripts directly and
# Airflow is deployed separately for orchestration.
COPY requirements.txt .
RUN grep -v "^apache-airflow" requirements.txt > requirements-runtime.txt \
    && pip install --upgrade pip \
    && pip install -r requirements-runtime.txt

COPY src/ ./src/
COPY config/ ./config/

RUN mkdir -p data/raw data/processed data/reports

# Full pipeline: ingest -> transform -> load
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
