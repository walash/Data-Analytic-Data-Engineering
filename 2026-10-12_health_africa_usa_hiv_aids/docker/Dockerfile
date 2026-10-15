# HIV/AIDS Africa & USA pipeline image
# Build from the project root:
#   docker build -f docker/Dockerfile -t hiv-aids-africa-usa:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Airflow is excluded: it pins sqlalchemy<2.0 and runs in its own scheduler image.
RUN pip install --upgrade pip \
    && grep -v '^apache-airflow' requirements.txt > /tmp/requirements-docker.txt \
    && pip install -r /tmp/requirements-docker.txt

COPY config/ ./config/
COPY src/ ./src/
COPY sql/ ./sql/
COPY dags/ ./dags/

RUN mkdir -p data/raw data/processed

CMD ["python", "src/ingestion/ingest.py"]
