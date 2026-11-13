# -----------------------------------------------------------------------------
# NCD Burden (Diabetes & CVD) - Africa vs USA : pipeline container
# Build from the project root:
#   docker build -f docker/Dockerfile -t africa-usa-ncd:latest .
# -----------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml \
    LOG_LEVEL=INFO

WORKDIR /app

# sqlite3 CLI lets you run sql/queries.sql inside the container
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

RUN mkdir -p data/raw data/processed data/reports \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

VOLUME ["/app/data"]

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
