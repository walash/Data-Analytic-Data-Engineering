# NCD Burden in Africa vs the USA — Diabetes & Cardiovascular Disease

**Domain:** Global Health · **Focus:** Africa (54 countries, 5 sub-regions) & United States · **Date:** 2026-11-13

An end-to-end data engineering project that ingests World Bank / WHO non-communicable disease (NCD) indicators,
cleans and enriches them, loads them into a SQLite star schema, and analyses how the burden of **diabetes** and
**cardiovascular / chronic-disease mortality** in Africa compares with the United States.

---

## Problem Statement

Non-communicable diseases — cardiovascular disease (CVD), diabetes, cancer and chronic respiratory disease — cause
around three-quarters of deaths worldwide. In the United States they dominate mortality. In Africa, health systems
built around infectious disease are now facing a fast-rising NCD wave: the **double burden of disease**.

This project answers:

1. How does adult diabetes prevalence in each African country and sub-region compare with the USA?
2. Where is diabetes growing fastest since 2011?
3. How far along the epidemiological transition is Africa — what share of deaths is now due to NCDs, versus
   North America?
4. Which countries combine high metabolic risk with NCD-dominant mortality and should be prioritised?

---

## Data Sources

| Indicator | Code | Source / URL |
|---|---|---|
| Diabetes prevalence (% of population ages 20–79) | `SH.STA.DIAB.ZS` | https://api.worldbank.org/v2/country/all/indicator/SH.STA.DIAB.ZS?format=json&per_page=500&mrv=10 |
| Cause of death by NCDs (% of total deaths) | `SH.DTH.NCOM.ZS` | https://api.worldbank.org/v2/country/all/indicator/SH.DTH.NCOM.ZS?format=json&per_page=500&mrv=10 |
| Mortality from CVD, cancer, diabetes or CRD, ages 30–70 (%) | `SH.DYN.NCOM.ZS` | https://api.worldbank.org/v2/country/all/indicator/SH.DYN.NCOM.ZS?format=json&per_page=500&mrv=10 |
| WHO Global Health Observatory — NCD theme (reference) | — | https://www.who.int/data/gho/data/themes/noncommunicable-diseases |

All sources are public; no API keys are needed.

**Committed raw snapshot (`data/raw/`):** `diabetes_prevalence.csv` (525 rows, survey years 2000/2011/2024) and
`ncd_deaths.csv` (455 rows, 2010–2021), plus the original API JSON. The NCD-deaths snapshot holds the first API page
only (aggregates + countries A–C), so the US is benchmarked using the **North America** aggregate for that
indicator. `SH.DYN.NCOM.ZS` was not reachable when the snapshot was taken. A fresh run of `ingest.py` paginates
through every page of all three indicators and closes both gaps — no code changes needed.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                     Apache Airflow  (dags/pipeline_dag.py)                    │
 │                schedule: 0 6 * * 1,3,5  (Mon/Wed/Fri 06:00 UTC)               │
 └──────────────────────────────────────────────────────────────────────────────┘
          │                    │                      │                    │
          ▼                    ▼                      ▼                    ▼
 ┌─────────────────┐  ┌──────────────────┐  ┌───────────────────┐  ┌───────────────┐
 │   1. INGEST     │  │  2. TRANSFORM    │  │     3. LOAD       │  │ 4. REPORT     │
 │ src/ingestion/  │─▶│ src/transform-   │─▶│ src/loading/      │─▶│ generate_     │
 │ ingest.py       │  │ ation/transform  │  │ load.py           │  │ report (DAG)  │
 │                 │  │ .py              │  │                   │  │               │
 │ • paginated GET │  │ • type casting   │  │ • star schema DDL │  │ • row counts  │
 │ • retry+backoff │  │ • null handling  │  │ • dim_country     │  │ • Africa vs   │
 │ • JSON + CSV    │  │ • range checks   │  │ • dim_indicator   │  │   USA summary │
 │ • cache fallback│  │ • dedup          │  │ • fact_ncd_obs    │  │ • markdown    │
 │                 │  │ • region tagging │  │ • v_africa_usa_ncd│  │   data/reports│
 │                 │  │ • YoY change/rank│  │ • integrity check │  │               │
 └────────┬────────┘  └────────┬─────────┘  └─────────┬─────────┘  └───────────────┘
          │                    │                      │
          ▼                    ▼                      ▼
   World Bank API ──▶   data/raw/*.csv  ──▶  data/processed/*.csv ──▶ data/ncd_health.db
   (SH.STA.DIAB.ZS,     *_worldbank.json     ncd_observations.csv          │
    SH.DTH.NCOM.ZS,                          countries.csv                 │
    SH.DYN.NCOM.ZS)                          indicators.csv                ▼
                                             africa_usa_ncd_wide.csv  ┌─────────────────────┐
                                                                      │ sql/queries.sql (8) │
                                                                      │ notebooks/          │
                                                                      │   analysis.ipynb    │
                                                                      └─────────────────────┘
   Docker (docker/) packages the pipeline · GitHub Actions (.github/workflows/ci.yml) lints + smoke-tests
```

### Data model

| Table | Grain | Key columns |
|---|---|---|
| `dim_country` | country / World Bank aggregate | `iso3`, `country_name`, `region_group` (Africa, USA, Benchmark, Aggregate, Other), `africa_subregion`, `is_aggregate` |
| `dim_indicator` | indicator | `indicator_code`, `indicator_key`, `indicator_name`, `first_year`, `last_year` |
| `fact_ncd_observation` | country × indicator × year | `value`, `prev_value`, `change_pp`, `change_pp_per_year`, `is_latest`, `africa_rank` |
| `v_africa_usa_ncd` (view) | as fact | facts joined to both dimensions, filtered to Africa + USA + benchmarks |

---

## Project Structure

```
2026-11-13_health_africa_usa_ncd_diabetes_cardiovascular/
├── .github/workflows/ci.yml        # flake8 lint + ingestion smoke test
├── config/config.yaml              # API endpoints, paths, pipeline params, region mapping
├── dags/pipeline_dag.py            # Airflow DAG: ingest → transform → load → generate_report
├── data/
│   ├── raw/                        # World Bank CSV + JSON snapshots
│   ├── processed/                  # cleaned, enriched CSVs
│   └── ncd_health.db               # SQLite star schema (generated)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml          # pipeline + sqlite shell services
├── notebooks/analysis.ipynb        # EDA with 8 visualisations
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```

---

## How to Run

### 1. Set up the environment
```bash
cd 2026-11-13_health_africa_usa_ncd_diabetes_cardiovascular
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage
```bash
python src/ingestion/ingest.py --dry-run          # validate config, print endpoints
python src/ingestion/ingest.py                    # fetch all 3 indicators (all pages)
python src/ingestion/ingest.py --indicators diabetes_prevalence --max-pages 1   # partial fetch
python src/transformation/transform.py            # → data/processed/
python src/loading/load.py                        # → data/ncd_health.db
```
If the API is unreachable, ingestion keeps the existing raw files and the later stages still run.

### 3. Query the warehouse
```bash
sqlite3 data/ncd_health.db < sql/queries.sql
```

### 4. Explore the notebook
```bash
jupyter notebook notebooks/analysis.ipynb
```

### 5. Run with Docker
```bash
docker compose -f docker/docker-compose.yml up --build pipeline
docker compose -f docker/docker-compose.yml run --rm sqlite sh -c "sqlite3 /data/ncd_health.db < /sql/queries.sql"
```

### 6. Orchestrate with Airflow
Airflow is installed separately (it is deliberately left out of `requirements.txt` because of its dependency
constraints):
```bash
pip install "apache-airflow==2.8.1" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.8.1/constraints-3.11.txt"
export NCD_PROJECT_ROOT=$(pwd)
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
airflow dags test africa_usa_ncd_diabetes_cardiovascular_pipeline 2026-11-13
```

### 7. Lint
```bash
flake8 src dags --max-line-length=100 --extend-ignore=E203,W503
```

---

## Key Insights (from the committed data snapshot)

| Metric | Value |
|---|---|
| USA diabetes prevalence (2024) | **13.7 %** (up from 9.4 % in 2011) |
| Mean across 54 African countries (2024) | **7.4 %** (median 5.75 %) |
| North Africa mean (2024) | **17.1 %** → 1.25× the US rate |
| West Africa mean (2024) | **5.1 %** → 0.37× the US rate |
| NCD share of deaths, Sub-Saharan Africa (2021) | **34.5 %** (27.7 % in 2010) |
| NCD share of deaths, North America (2021) | **78.1 %** (88.3 % in 2019, before COVID-19) |

1. **Six African countries exceed the US diabetes rate:** Egypt (22.4 %), Mauritius (20.1 %), Sudan (19.0 %),
   Algeria (17.5 %), Tunisia (16.0 %) and Libya (15.8 %).
2. **Diabetes rose in 40 of 53 African countries** with data for both 2011 and 2024. Algeria (+10.5 pp) and Sudan
   (+10.4 pp) saw the steepest climbs; the US rose +4.3 pp.
3. **Africa is mid-transition:** NCDs caused about a third of Sub-Saharan deaths in 2021, up ~7 pp since 2010.
   Algeria (73.9 %) and Cabo Verde (64.1 %) already show NCD-dominant mortality patterns.
4. **Diabetes and NCD mortality move together:** across African countries, benchmarks and regions with both
   indicators (n = 20), diabetes prevalence correlates with the NCD share of deaths at r ≈ 0.70.
5. **The 2020–2021 drop in NCD share is a COVID-19 artefact:** extra communicable-disease deaths shrink the NCD
   *share* even though NCD deaths did not fall.

---

## Technologies

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Ingestion | `requests`, `retry` (exponential backoff), World Bank Indicators API v2 |
| Transformation | `pandas`, `numpy` |
| Storage | SQLite (star schema, indexes, view) |
| Analysis | SQL window functions, Jupyter, `matplotlib`, `seaborn`, `plotly` |
| Orchestration | Apache Airflow (PythonOperator DAG, Mon/Wed/Fri 06:00) |
| Configuration | YAML (`pyyaml`), env-var overrides (`CONFIG_PATH`, `LOG_LEVEL`, `NCD_DB_PATH`) |
| Containerisation | Docker, Docker Compose |
| CI/CD | GitHub Actions (flake8, ingestion smoke test, transform/load test) |

---

*Data: World Bank World Development Indicators (IDF Diabetes Atlas; WHO Global Health Estimates). Figures are
modelled estimates; low reported prevalence in some countries may partly reflect limited screening.*
