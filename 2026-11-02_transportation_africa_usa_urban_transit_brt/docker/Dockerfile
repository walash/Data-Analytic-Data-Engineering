# Urban Transit & BRT Africa-USA pipeline image
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data \
    DB_PATH=/app/data/transit_analytics.db

WORKDIR /app

# Install dependencies first for layer caching. Airflow is excluded: the
# container runs the ETL scripts directly, and apache-airflow 2.8.0 must be
# installed with its official constraints file (it pins SQLAlchemy < 2.0).
COPY requirements.txt .
RUN grep -v "^apache-airflow" requirements.txt > requirements-pipeline.txt \
    && pip install --no-cache-dir -r requirements-pipeline.txt

COPY src/ ./src/
COPY config/ ./config/

# data/ is normally a mounted volume holding raw inputs and outputs
RUN mkdir -p /app/data/raw /app/data/processed /app/logs

CMD ["sh", "-c", "python src/ingestion/ingest.py && python src/transformation/transform.py && python src/loading/load.py"]
