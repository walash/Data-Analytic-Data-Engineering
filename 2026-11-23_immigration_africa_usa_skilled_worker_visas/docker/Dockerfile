# Africa-USA Skilled Worker Visa Flows - pipeline image
# Build from the project root:  docker build -f docker/Dockerfile -t africa-usa-migration .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DB_PATH=data/migration_analytics.db \
    LOG_LEVEL=INFO

WORKDIR /app

# Dependencies first for layer caching (apache-airflow is commented out in
# requirements.txt, so it is not installed here).
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

RUN mkdir -p data/raw data/processed

# Full pipeline: ingest -> transform -> load -> report
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py --report"]
