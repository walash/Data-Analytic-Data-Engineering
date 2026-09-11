# =====================================================================
# Africa-USA Air Cargo & Freight Routes -- Pipeline container image
# =====================================================================
FROM python:3.11-slim

# Avoid interactive prompts and enable unbuffered logging.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies first to leverage Docker layer caching.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source and configuration.
COPY src/ /app/src/
COPY config/ /app/config/

# Ensure the data directory exists (usually mounted as a volume at runtime).
RUN mkdir -p /app/data/raw /app/data/processed

# Run the full ETL pipeline: ingest -> transform -> load.
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
