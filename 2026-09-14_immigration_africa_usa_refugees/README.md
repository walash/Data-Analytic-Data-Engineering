# Africa-to-USA Immigration & Refugee Flows: A Data Engineering Pipeline

> **Domain:** Global Immigration &nbsp;•&nbsp; **Geographic focus:** Africa & USA

An end-to-end data engineering project that ingests, transforms, loads and
analyses migration, remittance and refugee data for 36 African countries,
together with asylum decisions rendered in the United States.

---

## Problem statement

Human migration out of Africa — and the asylum system that receives it in the
United States — is shaped by conflict, economic pressure and demographic change.
Yet the underlying data is fragmented across multiple public APIs with different
schemas and update cadences. This project builds a reproducible pipeline that
answers questions such as:

- Which African countries have the **highest net emigration**, and how has it
  trended over time?
- Which economies are **most dependent on remittances** (as a share of GDP), and
  how does that relate to emigration?
- How have **USA asylum decisions** (approvals, rejections, procedure types)
  evolved between 2010 and 2023?
- How does **global forced displacement** provide context for the USA caseload?
- How do **North Africa and Sub-Saharan Africa** differ in migration dynamics?

---

## Data sources (all public, no API key required)

| Source | Indicator / endpoint | URL |
|--------|----------------------|-----|
| World Bank Open Data | Net migration (`SM.POP.NETM`) | `https://api.worldbank.org/v2/country/{iso}/indicator/SM.POP.NETM?format=json` |
| World Bank Open Data | Personal remittances, % of GDP (`BX.TRF.PWKR.DT.GD.ZS`) | `https://api.worldbank.org/v2/country/{iso}/indicator/BX.TRF.PWKR.DT.GD.ZS?format=json` |
| World Bank Open Data | Total population (`SP.POP.TOTL`) | `https://api.worldbank.org/v2/country/{iso}/indicator/SP.POP.TOTL?format=json` |
| UNHCR Refugee Statistics | Global refugee population | `https://api.unhcr.org/population/v1/population/` |
| UNHCR Refugee Statistics | Asylum decisions (USA) | `https://api.unhcr.org/population/v1/asylum-decisions/` |

**African countries covered (36):** Somalia, Ethiopia, DR Congo, South Sudan,
Eritrea, Nigeria, Sudan, Kenya, Uganda, Ghana, Senegal, Cameroon, Zimbabwe,
Mali, Guinea, Algeria, Egypt, Morocco, Tunisia, Angola, Ivory Coast, Liberia,
Madagascar, Malawi, Mauritania, Niger, Rwanda, Sierra Leone, Togo, Burundi,
Central African Republic, Gambia, Guinea-Bissau, Lesotho, Namibia, Zambia.

---

## Architecture

```
                    ┌──────────────────────────────────────────────┐
                    │              EXTERNAL DATA SOURCES            │
                    │   World Bank Open Data API   UNHCR Stats API  │
                    └───────────────┬──────────────────┬───────────┘
                                    │                  │
                                    ▼                  ▼
        ┌───────────────────────────────────────────────────────────┐
        │  1. INGESTION  (src/ingestion/ingest.py)                    │
        │     • requests + tenacity retry/backoff                     │
        │     • writes JSON + CSV  ──────────────►  data/raw/         │
        └───────────────────────────┬───────────────────────────────┘
                                     ▼
        ┌───────────────────────────────────────────────────────────┐
        │  2. TRANSFORMATION  (src/transformation/transform.py)      │
        │     • clean, cast, dedupe, handle nulls (pandas)            │
        │     • merge migration + remittances + population           │
        │     • derive migration_rate_per_1000, remittance_per_capita│
        │     • region tagging  ─────────────────►  data/processed/  │
        └───────────────────────────┬───────────────────────────────┘
                                     ▼
        ┌───────────────────────────────────────────────────────────┐
        │  3. LOADING  (src/loading/load.py)                         │
        │     • SQLite schema + indexes                              │
        │     • data/immigration_analytics.db                       │
        │       tables: african_migration, remittances, population, │
        │               asylum_decisions, country_summary           │
        └───────────────────────────┬───────────────────────────────┘
                                     ▼
        ┌───────────────────────────────────────────────────────────┐
        │  4. ANALYSIS                                               │
        │     • sql/queries.sql        (8 analytical queries)        │
        │     • notebooks/analysis.ipynb (charts + narrative)        │
        └───────────────────────────────────────────────────────────┘

        Orchestration : dags/pipeline_dag.py   (Apache Airflow, Mon/Wed/Fri 06:00)
        Packaging     : docker/Dockerfile + docker-compose.yml
        CI            : .github/workflows/ci.yml  (flake8 + ingestion smoke test)
```

---

## How to run

### 1. Set up the environment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py          # fetch raw data -> data/raw/
python src/transformation/transform.py  # clean/merge   -> data/processed/
python src/loading/load.py              # load          -> data/immigration_analytics.db
```

> The repository already ships with real data in `data/raw/`, so you can run the
> transformation and loading stages immediately without network access.

### 3. Explore the results

```bash
# Run the analytical SQL queries
sqlite3 data/immigration_analytics.db < sql/queries.sql

# Or open the notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder (or point
`AIRFLOW__CORE__DAGS_FOLDER` at this repo's `dags/`) and enable the
`immigration_africa_usa_pipeline` DAG.

---

## Key insights

Derived from the shipped data (World Bank + UNHCR):

- **Largest net emigration (cumulative):** Sudan (≈ -1.05M), Zimbabwe (≈ -0.97M)
  and South Sudan (≈ -0.68M) top the list — driven by conflict and instability.
- **Highest remittance dependency:** Lesotho (~21% of GDP), The Gambia (~21%) and
  Liberia (~18%) lean heavily on remittances — the economic mirror of emigration.
- **USA asylum caseload** rose from ~0.73M decisions in 2010 to ~2.5M in 2023,
  tracking the global rise in displacement.
- **Global forced displacement** roughly tripled over the period: refugees grew
  from 10.5M (2010) to 31.6M (2023) and asylum seekers from 0.84M to 6.86M.
- **Regional contrast:** North Africa and Sub-Saharan Africa exhibit distinct
  migration balances and per-capita rates (see query 7).

---

## Technologies used

- **Python 3.11** — pipeline language
- **pandas / numpy** — data cleaning, merging, derived metrics
- **requests / tenacity** — resilient API ingestion with retry & backoff
- **SQLite** — analytical data store
- **matplotlib / seaborn / plotly** — visualisation
- **Jupyter** — interactive analysis
- **Apache Airflow** — scheduling & orchestration
- **Docker / docker-compose** — containerised, reproducible runs
- **GitHub Actions (flake8)** — linting & CI smoke tests

---

## Project structure

```
2026-09-14_immigration_africa_usa_refugees/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/           # raw API extracts (CSV + JSON)
│   └── processed/     # cleaned/merged CSVs (generated)
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
