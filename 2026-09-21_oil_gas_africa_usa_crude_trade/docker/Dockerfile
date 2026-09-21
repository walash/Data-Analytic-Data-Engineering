# Dockerfile for the Oil & Gas Africa-USA crude trade pipeline.
# Builds a self-contained image that can run the full ETL pipeline:
# ingest -> transform -> load.

FROM python:3.10-slim

# Prevent Python from writing .pyc files and enable unbuffered logging.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Data path environment variables consumed by the pipeline.
ENV DATA_RAW_PATH=/app/data/raw \
    DATA_PROCESSED_PATH=/app/data/processed \
    DB_PATH=/app/data/oil_gas.db

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir pandas requests numpy pyarrow pyyaml

# Copy the source code and configuration.
COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

# Ensure data directories exist (usually mounted as volumes at runtime).
RUN mkdir -p /app/data/raw /app/data/processed

# Run the full pipeline sequentially.
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
