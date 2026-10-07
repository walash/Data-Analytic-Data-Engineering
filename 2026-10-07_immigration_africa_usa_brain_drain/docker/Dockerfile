# Africa -> USA Brain Drain ETL pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t africa-usa-brain-drain:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO \
    CONFIG_PATH=/app/config/config.yaml

WORKDIR /app

# sqlite3 CLI lets you run sql/queries.sql inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config/ ./config/
COPY src/ ./src/
COPY sql/ ./sql/
COPY data/raw/ ./data/raw/

RUN mkdir -p data/processed reports \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

VOLUME ["/app/data"]

# Full ETL: ingest (World Bank API) -> transform -> load into data/brain_drain.db
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
