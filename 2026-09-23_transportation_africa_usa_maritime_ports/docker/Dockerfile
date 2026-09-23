# =============================================================================
# Dockerfile for the Africa-USA Maritime Port Trade data pipeline.
# Builds a self-contained image that runs the full ETL: ingest -> transform ->
# load. Build context is expected to be the project root.
# =============================================================================
FROM python:3.11-slim

# Improve logging and avoid .pyc clutter inside the container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        pandas==2.1.4 \
        requests==2.31.0 \
        numpy==1.26.2 \
        pyyaml==6.0.1

# Copy application source and configuration.
COPY src/ ./src/
COPY config/ ./config/

# Ensure data directories exist (usually mounted as volumes at runtime).
RUN mkdir -p data/raw data/processed

# Run the full pipeline sequentially: ingest -> transform -> load.
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
