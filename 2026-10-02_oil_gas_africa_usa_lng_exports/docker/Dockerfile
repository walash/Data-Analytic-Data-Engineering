# Africa LNG Exports to the USA - pipeline image
# Build from the project root:  docker build -f docker/Dockerfile -t africa-lng-pipeline .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CONFIG_PATH=/app/config/config.yaml \
    DB_PATH=/app/data/lng_pipeline.db

WORKDIR /app

# Install only the runtime dependencies needed by the pipeline stages
# (Airflow / Jupyter / dev tools are excluded to keep the image small).
COPY requirements.txt .
RUN grep -Ev '^(apache-airflow|jupyter|flake8|pytest)==' requirements.txt > requirements.runtime.txt \
    && pip install -r requirements.runtime.txt

COPY src/ ./src/
COPY config/ ./config/
COPY sql/ ./sql/
COPY data/raw/ ./data/raw/

RUN mkdir -p data/raw data/processed

# Full pipeline: ingest (falls back to bundled raw files if APIs are
# unreachable) -> transform -> load -> report.
CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py --report"]
