# AI-Powered Healthcare Diagnostics: Bridging the Africa-USA Health Gap
# Container image for the ETL pipeline (ingest -> transform -> load).
# Build from the project root:
#   docker build -f docker/Dockerfile -t ai-health-diagnostics:latest .

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO

WORKDIR /app

# Install only the runtime dependencies the pipeline needs; notebook and
# Airflow packages from requirements.txt are excluded to keep the image small.
COPY requirements.txt .
RUN grep -E '^(pandas|numpy|requests|pyyaml|sqlalchemy|scikit-learn)==' requirements.txt > requirements-pipeline.txt \
    && pip install -r requirements-pipeline.txt

COPY config/ ./config/
COPY src/ ./src/
COPY data/ ./data/

RUN mkdir -p data/raw data/processed data/reports

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
