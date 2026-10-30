# Natural Gas Pipelines — Africa & USA: ETL pipeline image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO

WORKDIR /app

# Install dependencies first to maximise layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code, configuration, SQL and the analysis notebook
COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/
COPY notebooks/ ./notebooks/

# Run as an unprivileged user; data/ is normally mounted as a volume
RUN mkdir -p data/raw data/processed reports \
    && useradd --create-home --uid 1000 pipeline \
    && chown -R pipeline:pipeline /app
USER pipeline

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
