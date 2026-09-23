# Africa-USA Maritime Port Trade & Shipping Connectivity Data Engineering Project

**Domain:** Global Transportation
**Geographic focus:** Africa (17 coastal nations) and the USA

An end-to-end data engineering pipeline that ingests, transforms, loads and
analyzes maritime shipping connectivity, container port throughput, logistics
performance, and trade-flow indicators for 17 African coastal nations and the
United States, using open data from the **World Bank Open Data API**.

---

## Problem Statement

Maritime transport carries the overwhelming majority of Africa's international
merchandise trade, yet shipping connectivity and port performance vary widely
across the continent. This project builds a reproducible pipeline to analyze:

- How well connected each African coastal nation is to global liner shipping
  networks (Liner Shipping Connectivity Index).
- Container port throughput trends and growth rates.
- Logistics performance (LPI) and how it relates to shipping connectivity.
- Merchandise trade intensity (% of GDP) and export volumes, comparing African
  nations against the USA benchmark.

The goal is to surface data-driven insights about maritime trade dynamics
between African coastal economies and the USA.

---

## Countries Covered

**African coastal nations (17):** Algeria (DZA), Angola (AGO), Cameroon (CMR),
Côte d'Ivoire (CIV), Egypt (EGY), Ethiopia (ETH), Ghana (GHA), Kenya (KEN),
Libya (LBY), Madagascar (MDG), Morocco (MAR), Mozambique (MOZ), Nigeria (NGA),
Senegal (SEN), South Africa (ZAF), Tanzania (TZA), Tunisia (TUN)

**Benchmark:** United States (USA)

---

## Data Sources

All data comes from the **World Bank Open Data API** (public, no API key
required). The pipeline requests the most recent values (`mrv`) per indicator:

| Indicator | Code | Endpoint |
|-----------|------|----------|
| Liner Shipping Connectivity Index | `IS.SHP.GCNW.XQ` | `https://api.worldbank.org/v2/country/{iso_list}/indicator/IS.SHP.GCNW.XQ?format=json&per_page=500&mrv=10` |
| Container Port Throughput (TEU) | `IS.SHP.GOOD.TU` | `https://api.worldbank.org/v2/country/{iso_list}/indicator/IS.SHP.GOOD.TU?format=json&per_page=500&mrv=10` |
| Merchandise Trade (% of GDP) | `TG.VAL.TOTL.GD.ZS` | `https://api.worldbank.org/v2/country/{iso_list}/indicator/TG.VAL.TOTL.GD.ZS?format=json&per_page=500&mrv=10` |
| Logistics Performance Index (overall) | `LP.LPI.OVRL.XQ` | `https://api.worldbank.org/v2/country/{iso_list}/indicator/LP.LPI.OVRL.XQ?format=json&per_page=500&mrv=5` |
| Exports of Goods & Services (US$) | `NE.EXP.GNFS.CD` | `https://api.worldbank.org/v2/country/{iso_list}/indicator/NE.EXP.GNFS.CD?format=json&per_page=500&mrv=10` |

`{iso_list}` is the semicolon-separated list of the 18 ISO-3 country codes.

---

## Architecture

```
              +---------------------------------------------------+
              |              World Bank Open Data API             |
              |  (Liner Shipping, Container TEU, Trade %GDP,       |
              |   Logistics Performance Index, Exports US$)        |
              +---------------------------------------------------+
                                   |
                                   |  HTTP GET (retry + backoff)
                                   v
                    +-------------------------------+
                    |   INGESTION  (src/ingestion)  |
                    |        ingest.py              |
                    +-------------------------------+
                                   |
                                   v
                    +-------------------------------+
                    |          data/raw/            |
                    |   5 tidy indicator CSVs       |
                    +-------------------------------+
                                   |
                                   v
                    +-------------------------------+
                    | TRANSFORMATION (src/transform)|
                    |        transform.py           |
                    |  clean -> merge -> derive     |
                    +-------------------------------+
                                   |
                                   v
                    +-------------------------------+
                    |        data/processed/        |
                    |  cleaned CSVs + master CSV    |
                    +-------------------------------+
                                   |
                                   v
                    +-------------------------------+
                    |    LOADING   (src/loading)    |
                    |          load.py              |
                    +-------------------------------+
                                   |
                                   v
                    +-------------------------------+
                    |   SQLite: data/maritime_ports.db |
                    |  maritime_indicators,         |
                    |  country_metadata,            |
                    |  yearly_summary               |
                    +-------------------------------+
                                   |
                        +----------+----------+
                        v                     v
             +--------------------+  +----------------------+
             |   sql/queries.sql  |  | notebooks/analysis   |
             |   (8 analyses)     |  |  .ipynb (charts)     |
             +--------------------+  +----------------------+

  Orchestration: dags/pipeline_dag.py (Airflow, @weekly)
  Packaging:     docker/Dockerfile + docker-compose.yml
  CI:            .github/workflows/ci.yml (flake8 + dry-run smoke test)
```

---

## Project Structure

```
2026-09-23_transportation_africa_usa_maritime_ports/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/                    # source indicator CSVs
│   └── processed/              # cleaned + master CSVs (generated)
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/
│   └── queries.sql
├── notebooks/
│   └── analysis.ipynb
├── dags/
│   └── pipeline_dag.py
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/
    └── workflows/ci.yml
```

---

## How to Run

### 1. Set up the environment

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
# Ingest raw data from the World Bank API -> data/raw/
python src/ingestion/ingest.py

# Clean, merge, derive metrics -> data/processed/
python src/transformation/transform.py

# Load the master dataset into SQLite -> data/maritime_ports.db
python src/loading/load.py
```

Validate configuration without downloading (used by CI):

```bash
python src/ingestion/ingest.py --dry-run
```

### 3. Explore the results

```bash
# Run the analytical SQL queries
sqlite3 data/maritime_ports.db < sql/queries.sql

# Or open the analysis notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder (or point
`AIRFLOW__CORE__DAGS_FOLDER` at this project's `dags/`) and enable the
`maritime_ports_pipeline` DAG. It runs weekly:
`ingest_data → transform_data → load_to_db → generate_report`.

---

## Key Insights

Based on the ingested World Bank data:

- **South Africa, Egypt and Morocco lead African liner shipping connectivity.**
  Their location on major East-West and Mediterranean routes gives them the
  highest Liner Shipping Connectivity Index scores on the continent.
- **Nigeria leads West African container throughput.** Its large domestic
  economy drives high container port volumes relative to regional peers.
- **The USA has the highest Logistics Performance Index (LPI)** among all
  countries analyzed, underscoring the performance gap between the US benchmark
  and most African ports.
- **Merchandise trade as a share of GDP is far higher in many African economies
  than in the USA**, reflecting how central external trade is to African
  economic activity even though absolute export volumes remain much smaller.
- **Shipping connectivity and logistics performance are positively associated:**
  countries with higher LPI scores also tend to be better connected to global
  liner networks.

---

## Technologies

- **Python** — pipeline logic
- **pandas / numpy** — data cleaning and transformation
- **SQLite** — analytical data store
- **Apache Airflow** — workflow orchestration
- **Docker / docker-compose** — reproducible packaging
- **Jupyter** — interactive analysis
- **matplotlib / seaborn / plotly** — visualization
- **flake8 / pytest / GitHub Actions** — linting, testing, CI
