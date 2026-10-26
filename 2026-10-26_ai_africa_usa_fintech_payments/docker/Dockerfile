# AI-Driven Fintech & Digital Payments: Africa and USA - pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t fintech-africa-usa:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml \
    LOG_LEVEL=INFO

WORKDIR /app

# Install dependencies first to take advantage of Docker layer caching
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy project files
COPY config/ ./config/
COPY src/ ./src/
COPY sql/ ./sql/
COPY dags/ ./dags/
COPY notebooks/ ./notebooks/

# data/ is normally mounted as a volume so outputs persist on the host
RUN mkdir -p /app/data/raw /app/data/processed /app/reports

# Run the full pipeline: ingest -> transform -> load
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
