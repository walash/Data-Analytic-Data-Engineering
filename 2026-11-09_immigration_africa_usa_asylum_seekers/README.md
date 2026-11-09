# African Asylum Seekers & Immigration Flows to the USA: A Data Engineering Pipeline

| | |
|---|---|
| **Domain** | Immigration |
| **Geographic focus** | Africa (53 countries) & the United States |
| **Period** | 2021 – 2025 (World Bank most-recent values) |
| **Stack** | Python · pandas · SQLite · Airflow · Docker · GitHub Actions |

## Problem Statement

Conflict, economic shocks and climate stress are pushing growing numbers of Africans to leave home, and the
United States is one of their main long-haul destinations for asylum and resettlement. The data needed to
follow these flows is spread across several public sources with different formats and update cycles.

This project builds a reproducible **ETL pipeline** that ingests, cleans and models migration indicators for
every African country plus the USA so analysts can answer questions such as:

- Which African countries produce the largest net emigration (the pool of potential asylum seekers)?
- How have African outflows and US net immigration changed year over year?
- Which sub-regions (North, West, East, Central, Southern Africa) drive displacement?
- How dependent are African economies on remittances from their diaspora, and how does this relate to migrant stock?
- How large is Africa's emigration pool compared with annual US immigration intake?

## Data Sources

| Source | Indicator / dataset | URL |
|---|---|---|
| World Bank Open Data API | Net migration (`SM.POP.NETM`) | https://api.worldbank.org/v2/country/all/indicator/SM.POP.NETM?format=json |
| World Bank Open Data API | International migrant stock, total (`SM.POP.TOTL`) | https://api.worldbank.org/v2/country/all/indicator/SM.POP.TOTL?format=json |
| World Bank Open Data API | International migrant stock, % of population (`SM.POP.TOTL.ZS`) | https://api.worldbank.org/v2/country/all/indicator/SM.POP.TOTL.ZS?format=json |
| World Bank Open Data API | Personal remittances received, % of GDP (`BX.TRF.PWKR.DT.GD.ZS`) | https://api.worldbank.org/v2/country/all/indicator/BX.TRF.PWKR.DT.GD.ZS?format=json |
| UNHCR Refugee Data Finder | Asylum applications & refugee populations (context) | https://www.unhcr.org/refugee-statistics/ |
| DHS / USCIS Yearbook of Immigration Statistics | US asylum, refugee & LPR statistics (context) | https://www.dhs.gov/immigration-statistics/yearbook |

The World Bank API is open — no API key is required. The raw extracts used for the analysis are committed in
`data/raw/` (`africa_usa_migration_data.csv`, `africa_remittances_migration.csv` and the original
`wb_*.json` API responses).

## Architecture

```
 ┌──────────────────────────────┐   ┌──────────────────────────┐
 │  World Bank Open Data API    │   │ UNHCR / DHS Yearbook     │
 │  SM.POP.NETM  SM.POP.TOTL    │   │ (asylum context,         │
 │  SM.POP.TOTL.ZS  BX.TRF...   │   │  reference only)         │
 └──────────────┬───────────────┘   └────────────┬─────────────┘
                │ HTTPS/JSON (paged, retried)    │
                ▼                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                         │
 │    requests + tenacity retry · filter 53 African ISO3 + USA │
 │    --dry-run for CI                    ──► data/raw/*.csv   │
 └──────────────┬──────────────────────────────────────────────┘
                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM  src/transformation/transform.py               │
 │    type casting · null handling · dedup · sub-region tags   │
 │    migration_intensity · remittance_dependency_category     │
 │                                  ──► data/processed/*.csv   │
 └──────────────┬──────────────────────────────────────────────┘
                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py      SQLite warehouse         │
 │    migration_flows · remittance_flows · country_profiles    │
 │                         ──► data/africa_usa_immigration.db  │
 └──────────────┬───────────────────────────────┬──────────────┘
                ▼                               ▼
 ┌──────────────────────────────┐ ┌─────────────────────────────┐
 │ sql/queries.sql (8 queries)  │ │ notebooks/analysis.ipynb    │
 │ CTEs · window functions      │ │ matplotlib/seaborn/plotly   │
 └──────────────────────────────┘ └─────────────────────────────┘

 Orchestration: Airflow DAG `africa_usa_asylum_immigration_pipeline` (@weekly)
     ingest_data ──► transform_data ──► load_data ──► generate_report
 Packaging: Docker / docker-compose     CI: GitHub Actions (flake8 + dry-run smoke test)
```

## Project Structure

```
2026-11-09_immigration_africa_usa_asylum_seekers/
├── .github/workflows/ci.yml        # lint + smoke test
├── config/config.yaml              # API, paths, country lists, thresholds
├── dags/pipeline_dag.py            # Airflow DAG
├── data/
│   ├── raw/                        # World Bank extracts (CSV + JSON)
│   └── processed/                  # cleaned outputs (generated)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA with 8 visualisations
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```

## How to Run

### 1. Set up the environment

```bash
cd 2026-11-09_immigration_africa_usa_asylum_seekers
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run     # validate config & print API URLs
python src/ingestion/ingest.py               # live download -> data/raw/
python src/transformation/transform.py       # -> data/processed/
python src/loading/load.py                   # -> data/africa_usa_immigration.db
```

`ingest.py --output-dir <dir>` writes to another folder if you want to keep the committed raw snapshot unchanged.
All paths can be overridden with the `CONFIG_PATH`, `RAW_DATA_DIR`, `PROCESSED_DATA_DIR` and `DB_PATH`
environment variables.

### 3. Explore the results

```bash
sqlite3 -header -column data/africa_usa_immigration.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build pipeline           # full ETL
docker compose run --rm sqlite               # interactive SQLite shell (tools profile)
```

### 5. Orchestrate with Airflow

```bash
pip install "apache-airflow==2.8.1" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.8.1/constraints-3.11.txt"
export AIRFLOW_HOME=~/airflow
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
export PROJECT_ROOT=$(pwd)
airflow standalone   # then trigger `africa_usa_asylum_immigration_pipeline`
```

## Data Model

| Table | Grain | Key columns |
|---|---|---|
| `migration_flows` | country × year (53 African countries + USA) | `net_migration`, `net_migration_yoy_change`, `migration_direction`, `migrant_stock_total`, `migrant_stock_filled`, `migration_intensity`, `subregion` |
| `remittance_flows` | country × year (African countries with remittance data) | `remittances_pct_gdp`, `remittance_dependency_category` (high ≥ 10 %, medium ≥ 3 %, low < 3 %, unknown) |
| `country_profiles` | country | cumulative/average/latest net migration, latest migrant stock, average remittances, latest dependency category |

## Key Insights (World Bank data, 2021–2025)

1. **Sudan is the epicentre of African displacement**: about **-1.60 million** cumulative net migration;
   **-1.35 million** in its worst single year, after the 2023 civil war began.
2. **Top net-emigration countries**: Sudan, Uganda (-439K), Zimbabwe (-390K), Nigeria (-246K), Morocco (-229K),
   Mozambique (-199K), Mali (-197K), Togo (-167K), DR Congo (-149K), Central African Republic (-148K).
3. **Africa was a net emigration continent every year**: aggregate net migration fell from about **-81K (2021)**
   to **-649K (2024)**, then eased to **-382K (2025)**.
4. **The USA absorbs the largest flows**: US net migration rose from **675K (2021)** to **1.32M (2022–2023)**
   and was **1.23M in 2025**. In 2023, Africa's gross emigration pool (about 2.0M) was 1.5× annual US net intake.
5. **North and East Africa drive outflows**: together nearly **90 %** of gross African net emigration, with
   West Africa (Nigeria, Mali, Togo) about one fifth.
6. **Remittance dependency is concentrated in small island states**: Comoros (~21.7 % of GDP) and Cabo Verde
   (~13.2 %) are "high"; Burundi and DR Congo are "medium". Correlation between migrant stock and remittances is
   weak and negative (r ≈ -0.34, 12 countries).
7. **Most African migrants stay in Africa**: Côte d'Ivoire hosts ~2.88M international migrants (9 % of its
   population), and Chad ~1.27M, largely refugees from Sudan.

**Limitations:** World Bank net migration covers all destinations (not only the USA); migrant stock is
published for benchmark years and, in this extract, for a subset of countries; and asylum application counts
come from the UNHCR and DHS sources, which are listed here as context only and are not ingested by the pipeline.

## Technologies Used

- **Python 3.11**: pipeline code
- **requests + tenacity**: resilient, paged World Bank API ingestion with exponential-backoff retries
- **pandas / NumPy**: cleaning, type casting, deduplication, feature derivation
- **SQLite**: lightweight analytical warehouse (CTEs and window functions in `sql/queries.sql`)
- **matplotlib, seaborn, plotly**: exploratory visualisation in Jupyter
- **PyYAML**: centralised configuration
- **Apache Airflow 2.8**: weekly orchestration
- **Docker & docker-compose**: containerised, reproducible runs
- **GitHub Actions**: flake8 linting and dry-run smoke tests
