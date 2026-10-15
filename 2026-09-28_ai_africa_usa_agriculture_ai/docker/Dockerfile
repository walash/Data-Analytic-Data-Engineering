# AI in Agriculture — data pipeline image
FROM python:3.11-slim

# System dependencies required for building wheels and SQLite access.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libsqlite3-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/airflow

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the full project into the image.
COPY . .

# Ensure the data directory tree exists (usually mounted as a volume at runtime).
RUN mkdir -p data/raw data/processed data/reports

# Environment configuration.
ENV PYTHONPATH=/opt/airflow \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/opt/airflow/data

# Default command runs the ingestion stage; override to run other stages.
CMD ["python", "src/ingestion/ingest.py"]
