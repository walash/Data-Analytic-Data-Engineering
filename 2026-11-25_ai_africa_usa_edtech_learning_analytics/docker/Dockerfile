# Container image for the AI-Powered EdTech & Learning Analytics pipeline.
# Build from the project root:
#   docker build -f docker/Dockerfile -t edtech-learning-analytics .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    CONFIG_PATH=/app/config/config.yaml \
    LOG_LEVEL=INFO

WORKDIR /app

# Only the runtime libraries are needed inside the container; the full
# requirements.txt (Airflow, Jupyter) is used for local development.
COPY requirements.txt .
RUN grep -E '^(requests|pandas|numpy|pyyaml)==' requirements.txt > requirements-runtime.txt \
    && pip install --upgrade pip \
    && pip install -r requirements-runtime.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/

RUN mkdir -p data/raw data/processed

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
