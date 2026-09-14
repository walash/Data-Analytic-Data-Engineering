# Containerised runtime for the Africa-to-USA immigration data pipeline.
FROM python:3.11-slim

# Keep Python output unbuffered and skip .pyc files for cleaner container logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/config.yaml

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source and configuration.
COPY src/ /app/src/
COPY config/ /app/config/
COPY sql/ /app/sql/

# Ensure the data directories exist (usually mounted as volumes at runtime).
RUN mkdir -p /app/data/raw /app/data/processed

# Default command: run the ingestion stage. The full ETL can be run via
# docker-compose (see docker/docker-compose.yml) or by overriding CMD.
CMD ["python", "src/ingestion/ingest.py"]
