# African Asylum Seekers & Immigration Flows to the USA - pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t africa-usa-asylum-pipeline .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CONFIG_PATH=/app/config/config.yaml \
    RAW_DATA_DIR=/app/data/raw \
    PROCESSED_DATA_DIR=/app/data/processed \
    DB_PATH=/app/data/africa_usa_immigration.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/
COPY data/raw/ ./data/raw/

RUN mkdir -p /app/data/raw /app/data/processed

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
