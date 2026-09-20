# Container image for the Malaria & Infectious Disease Burden pipeline.
FROM python:3.11-slim

# Faster, cleaner logs and no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, data and configuration.
COPY src/ ./src/
COPY config/ ./config/
COPY data/ ./data/

# Ensure raw/processed directories exist (they are typically mounted as volumes).
RUN mkdir -p data/raw data/processed

# Run the full ETL pipeline: ingest -> transform -> load.
CMD ["sh", "-c", "python -m src.ingestion.ingest && python -m src.transformation.transform && python -m src.loading.load"]
