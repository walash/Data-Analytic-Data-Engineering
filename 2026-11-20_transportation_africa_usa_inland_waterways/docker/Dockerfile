# Inland Waterways (Africa & USA) pipeline image
# Build from the project root:  docker build -f docker/Dockerfile -t inland-waterways .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    DB_PATH=/app/data/waterways.db \
    LOG_LEVEL=INFO

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/raw /app/data/processed /app/data/reports

CMD ["python", "src/ingestion/ingest.py"]
