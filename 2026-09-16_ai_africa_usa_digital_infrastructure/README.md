# AI Adoption & Digital Infrastructure: Africa vs USA Data Engineering Pipeline

An end-to-end, reproducible data engineering pipeline that ingests, transforms,
loads and analyses World Bank digital-infrastructure indicators to quantify the
**digital divide and AI-readiness gap** between 43 African countries and the
United States.

- **Domain:** AI / Digital Infrastructure
- **Geographic focus:** 43 African countries + USA (44 economies)
- **Data period:** 2015–2024 (most recent 10 years per series)

---

## Problem Statement

Artificial intelligence at scale depends on foundational digital
infrastructure — internet access, mobile and broadband connectivity, a
high-tech industrial base and research investment. These foundations are
distributed extremely unevenly across the globe.

This project builds a data pipeline to measure and compare the **AI-readiness
gap** between African nations and the USA. Using five core World Bank
indicators, it answers questions such as:

- How large is the internet-penetration gap between Africa and the USA?
- Which African countries are closing the gap fastest?
- How "mobile-first" is African connectivity compared with the USA?
- Which economies lead Africa in high-technology exports and R&D intensity?
- How do the four African sub-regions (East, West, North, Southern) compare on a
  composite digital-readiness score?

---

## Data Sources

All data comes from the free **World Bank Open Data API** (no API key required):

```
https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json&per_page=500&mrv=10
```

| Indicator code      | Description                                             |
|---------------------|---------------------------------------------------------|
| `IT.NET.USER.ZS`    | Individuals using the Internet (% of population)         |
| `IT.CEL.SETS.P2`    | Mobile cellular subscriptions (per 100 people)          |
| `IT.NET.BBND.P2`    | Fixed broadband subscriptions (per 100 people)          |
| `TX.VAL.TECH.MF.ZS` | High-technology exports (% of manufactured exports)     |
| `GB.XPD.RSDV.GD.ZS` | Research & development expenditure (% of GDP)           |

**Countries (ISO-3):** AGO, BEN, BFA, BWA, CIV, CMR, COM, CPV, DJI, DZA, EGY,
ERI, ETH, GHA, GIN, GMB, KEN, LBR, LBY, MAR, MDG, MLI, MOZ, MUS, MWI, NAM, NER,
NGA, RWA, SDN, SEN, SLE, SOM, SSD, STP, TGO, TUN, TZA, UGA, USA, ZAF, ZMB, ZWE.

---

## Architecture

```
                 AI / DIGITAL INFRASTRUCTURE ETL PIPELINE
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  DATA SOURCES                                                              │
 │  World Bank Open Data API  (5 indicators × 44 countries × ~10 years)      │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      │  requests + retry/backoff
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  INGESTION            src/ingestion/ingest.py                             │
 │  Fetch each indicator, paginate, normalise → tidy CSV                     │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  RAW STORAGE          data/raw/*.csv                                      │
 │  internet_users · mobile_subscriptions · fixed_broadband ·               │
 │  hightech_exports · rnd_expenditure                                       │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  TRANSFORMATION       src/transformation/transform.py                    │
 │  Merge to wide panel · clean nulls · cast types · dedupe ·               │
 │  derive internet_mobile_ratio + digital_readiness_score · tag region     │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  PROCESSED STORAGE    data/processed/                                     │
 │  digital_infrastructure.csv (panel) · country_summary.csv (latest year)  │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  LOADING              src/loading/load.py                                 │
 │  Create schema · load tables · build indexes                             │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  SQLITE DB            data/digital_infrastructure.db                      │
 │  tables: digital_metrics · country_summary                               │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                      ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  ANALYSIS / REPORTING                                                     │
 │  sql/queries.sql (8 analytical queries) · notebooks/analysis.ipynb       │
 └──────────────────────────────────────────────────────────────────────────┘

   Orchestration: dags/pipeline_dag.py (Airflow, @weekly)
   ingest_data → transform_data → load_data → generate_report
```

---

## How to Run

### 1. Set up the environment

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
# Ingest raw indicators from the World Bank API into data/raw/
python src/ingestion/ingest.py

# Merge + clean + enrich into data/processed/
python src/transformation/transform.py

# Load processed CSVs into data/digital_infrastructure.db
python src/loading/load.py
```

> Tip: `python src/ingestion/ingest.py --dry-run` validates configuration and
> API connectivity without writing any files.

### 3. Explore the results

```bash
# Run the eight analytical SQL queries
sqlite3 data/digital_infrastructure.db < sql/queries.sql

# Or open the interactive notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build          # runs the full ETL
# Optional: browse the DB at http://localhost:8080 (sqlite-viewer service)
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder (or point
`AIRFLOW__CORE__DAGS_FOLDER` at this repo's `dags/`), then enable the
`ai_digital_infrastructure_pipeline` DAG. It runs weekly:
`ingest_data → transform_data → load_data → generate_report`.

---

## Key Insights

Derived from the loaded data (latest available year per country):

- **A wide connectivity gap.** African internet penetration averages **~43%**
  versus the USA's **~95%** — roughly a 50-percentage-point AI-readiness gap in
  basic access alone.
- **Africa is mobile-first.** African mobile subscriptions average **~103 per
  100 people** while fixed broadband averages just **~3.8 per 100** — a
  mobile-to-broadband ratio far higher than the USA's, confirming that African
  connectivity is overwhelmingly delivered over cellular networks rather than
  fixed lines.
- **North Africa leads the continent.** Morocco (~91%), Libya, Algeria, Tunisia
  and Egypt post the highest internet-penetration figures, giving North Africa
  the strongest average digital-readiness score of the four sub-regions.
- **Fast catch-up growth.** Several African economies (e.g. Libya, Algeria,
  Morocco) added double-digit percentage points of internet penetration in
  single years, narrowing the gap much faster than saturated markets like the
  USA can.
- **Tech-export base is thin but uneven.** High-technology exports as a share of
  manufactured exports are led in Africa by a handful of countries (e.g. Benin,
  Angola, South Africa), but most nations remain far below the diversified US
  industrial base.
- **R&D investment is the deepest gap.** Few African countries report R&D
  spending above ~1% of GDP, versus the USA's multi-percent intensity —
  signalling that the innovation-capacity gap underlying AI is even larger than
  the connectivity gap.

---

## Technologies

- **Python 3.10** — pipeline implementation
- **pandas / numpy** — data wrangling and feature engineering
- **requests** — World Bank API ingestion with retry/backoff
- **SQLite + SQLAlchemy** — analytical data store
- **Apache Airflow** — weekly pipeline orchestration
- **Docker / docker-compose** — reproducible, portable execution
- **Jupyter** — interactive analysis notebook
- **plotly / seaborn / matplotlib** — interactive and statistical visualisation
- **flake8 + GitHub Actions** — linting and CI

---

## Project Structure

```
2026-09-16_ai_africa_usa_digital_infrastructure/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/                     # ingested indicator CSVs
│   ├── processed/               # merged panel + country summary
│   └── digital_infrastructure.db
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/
│   └── queries.sql              # 8 analytical queries
├── notebooks/
│   └── analysis.ipynb           # 6+ visualisations
├── dags/
│   └── pipeline_dag.py          # Airflow DAG
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/
    └── workflows/ci.yml         # flake8 lint + import smoke test
```
