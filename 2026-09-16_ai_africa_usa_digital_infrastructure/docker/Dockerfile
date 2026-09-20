# Container image for the AI Adoption & Digital Infrastructure ETL pipeline.
FROM python:3.10-slim

# Predictable, unbuffered logging and no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/config.yaml

WORKDIR /app

# System build deps kept minimal; installed then cleaned to keep the image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir \
        pandas==2.1.4 \
        requests==2.31.0 \
        pyyaml==6.0.1 \
        sqlalchemy==2.0.25 \
        numpy==1.26.3

# Application code and configuration.
COPY src/ ./src/
COPY config/ ./config/

# Ensure data directories exist (typically mounted as volumes at runtime).
RUN mkdir -p data/raw data/processed

# Run the full ETL sequence: ingest -> transform -> load.
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
