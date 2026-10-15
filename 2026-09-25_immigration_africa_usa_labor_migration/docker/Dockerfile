# Container image for the Africa-to-USA Labor Migration data pipeline.
FROM python:3.11-slim

# Keep Python output unbuffered and avoid .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and configuration.
COPY src/ ./src/
COPY config/ ./config/

# Ensure data directories exist (typically mounted as volumes at runtime).
RUN mkdir -p data/raw data/processed

# Default command runs the ingestion stage.
CMD ["python", "src/ingestion/ingest.py"]
