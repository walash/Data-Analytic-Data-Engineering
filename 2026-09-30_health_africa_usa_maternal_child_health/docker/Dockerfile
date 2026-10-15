# Maternal & Child Health (Africa vs USA) - pipeline image
# Build from the project root:  docker build -f docker/Dockerfile -t mch-pipeline .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml \
    RAW_DATA_DIR=/app/data/raw \
    PROCESSED_DATA_DIR=/app/data/processed \
    DB_PATH=/app/data/db/maternal_child_health.db

WORKDIR /app

# Install dependencies first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy pipeline code and configuration
COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

RUN mkdir -p /app/data/raw /app/data/processed /app/data/db /app/data/reports \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

CMD ["python", "src/ingestion/ingest.py"]
