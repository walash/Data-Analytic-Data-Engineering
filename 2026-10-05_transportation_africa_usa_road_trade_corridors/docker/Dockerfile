# Africa-USA Road Infrastructure & Trade Corridors - ETL pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t africa-usa-road-trade:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only the runtime dependencies needed by the ETL scripts
# (Airflow/Jupyter stay out of the slim image).
COPY requirements.txt .
RUN grep -E '^(requests|pandas|numpy|sqlalchemy|pyyaml)==' requirements.txt > requirements-runtime.txt \
    && pip install --upgrade pip \
    && pip install -r requirements-runtime.txt

COPY src/ ./src/
COPY config/ ./config/

RUN mkdir -p data/raw data/processed

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
