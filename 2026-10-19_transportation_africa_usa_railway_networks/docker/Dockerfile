# Africa & USA Railway Networks — pipeline image
# Build from the project root:  docker build -f docker/Dockerfile -t africa-usa-railway-networks .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CONFIG_PATH=/app/config/config.yaml \
    RAILWAY_DB_PATH=/app/data/railway_networks.db

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first to maximise layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/
COPY dags/ ./dags/

RUN mkdir -p data/raw data/processed reports

# Full pipeline: ingest -> transform -> load
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
