# Inland Waterways Transportation: Africa & USA Data Engineering Pipeline

**Domain:** Global Transportation
**Geographic Focus:** Africa (Nigeria, DR Congo, Egypt, Ghana, Mali, plus 12 more countries for comparison) and the USA (Mississippi and Ohio River systems)

An end-to-end, reproducible ETL pipeline. It ingests open transport, economic and waterway data, then cleans, enriches and merges it. The results go into a SQLite warehouse and are analysed with SQL and a Jupyter notebook. Airflow orchestrates the pipeline, Docker packages it, and GitHub Actions checks it on every push.

---

## Problem Statement

Africa has some of the largest river systems on Earth: the Congo, Niger, Nile, Volta, Benue and Zambezi. The 17 African countries in this study have about **38,000 km of navigable inland waterways**, roughly 93% of the **41,009 km** US network. Yet inland water transport carries only a small share of African freight. Most cargo still moves by road, which is expensive, congested and carbon-intensive.

The United States runs one of the world's most developed inland waterway systems. The Mississippi River System and the Ohio River together move about **250 million tons** of coal, grain and petroleum each year.

This project measures that gap and asks:

1. How do African and US waterway networks compare in length, navigability and density?
2. How intensively is each network used (tons per navigable km, tons per 1,000 people)?
3. How have African river freight and US commodity flows changed over time?
4. Does waterway infrastructure relate to seaport container traffic and economic size?
5. Which African corridors (Nile, Congo, Niger, Volta) have the most untapped potential?

---

## Data Sources

| Dataset | File in `data/raw/` | Source |
|---|---|---|
| Container port traffic (TEU), `IS.SHP.GOOD.TU` | `container_port_traffic.csv` | World Bank Open Data API: https://api.worldbank.org/v2/ |
| GDP (current US$), `NY.GDP.MKTP.CD` | `gdp_data.csv` | World Bank Open Data API: https://api.worldbank.org/v2/ |
| Population, `SP.POP.TOTL` | `population.csv` | World Bank Open Data API: https://api.worldbank.org/v2/ (cross-referenced with UN DESA Population Division: https://population.un.org/wpp/) |
| Liner shipping connectivity index, `IS.SHP.GCNW.XQ` (freight proxy) | `freight_transport.csv` | World Bank Open Data API: https://api.worldbank.org/v2/ (UNCTAD-derived: https://unctad.org/topic/transport-and-trade-logistics/review-of-maritime-transport) |
| US inland waterway tonnage by commodity | `us_waterway_freight.csv` | US Army Corps of Engineers Waterborne Commerce Statistics: https://www.iwr.usace.army.mil/Missions/Navigation/Waterborne-Commerce-Statistics/ |
| African river freight and passengers | `africa_waterway_freight.csv` | African Development Bank Transport Data: https://www.afdb.org/en/topics-and-sectors/sectors/transport |
| Inland waterway length and navigable km | `inland_waterway_infrastructure.csv` | AfDB transport sector profiles and UNCTAD Review of Maritime Transport |

No API keys are required. The World Bank API is fully open.

> Note: `freight_transport.csv` keeps the column name `freight_million_ton_km` for schema compatibility, but it actually holds the World Bank *Liner Shipping Connectivity Index*. The transformation stage renames it to `liner_connectivity_index`.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                               RAW DATA                                   │
 │  World Bank API (TEU, GDP, population, LSCI)   USACE WCS   AfDB / UNCTAD │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 │  HTTP + retries (3x, exponential backoff)
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  INGESTION      src/ingestion/ingest.py        (--dry-run supported)     │
 │  -> data/raw/*.csv                                                       │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  TRANSFORMATION src/transformation/transform.py                          │
 │  clean (nulls, types, dedup) -> normalize (per-capita, tons/km)          │
 │  -> enrich (region, decade, waterway density) -> merge                   │
 │  -> data/processed/*.csv                                                 │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  LOADING        src/loading/load.py                                      │
 │  CREATE TABLE IF NOT EXISTS + idempotent transactional reload            │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  SQLite DB      data/waterways.db                                        │
 │  waterway_infrastructure | us_freight | africa_freight |                 │
 │  container_traffic | country_metrics                                     │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  ANALYSIS       sql/queries.sql (8 queries)  |  notebooks/analysis.ipynb │
 └──────────────────────────────────────────────────────────────────────────┘

   Orchestration: Airflow DAG `inland_waterways_pipeline` (@weekly)
   ingest_data >> transform_data >> load_data >> generate_report
```

---

## Project Structure

```
2026-11-20_transportation_africa_usa_inland_waterways/
├── .github/workflows/ci.yml        # flake8, py_compile, ingestion dry run
├── config/config.yaml              # API, paths, countries, indicators, schedule
├── dags/pipeline_dag.py            # Airflow DAG (weekly)
├── data/
│   ├── raw/                        # source CSVs
│   ├── processed/                  # cleaned and enriched CSVs
│   └── waterways.db                # SQLite warehouse
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA and 6 visualizations
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```

### Warehouse tables

| Table | Grain | Key metrics |
|---|---|---|
| `waterway_infrastructure` | country | total and navigable km, navigable share, waterway density (km per 1,000 sq km) |
| `us_freight` | year × waterway × commodity | tonnage (million and thousand tons) |
| `africa_freight` | country × waterway × year | freight, passengers, ports, utilization ratio, freight per 1,000 people |
| `container_traffic` | country × year | container TEU, liner connectivity index |
| `country_metrics` | country × year | GDP, population, GDP per capita, TEU per 1,000 people, waterway freight, utilization |

---

## How to Run

All commands run from the project root.

### 1. Local setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run   # preview the API calls, no I/O
python src/ingestion/ingest.py             # World Bank API + static datasets -> data/raw/
python src/transformation/transform.py     # -> data/processed/
python src/loading/load.py                 # -> data/waterways.db
```

Optional environment overrides: `DATA_DIR` (base data folder), `DB_PATH` (SQLite file), `LOG_LEVEL` (`DEBUG`, `INFO`, ...), `CONFIG_PATH`.

### 3. Query the warehouse

```bash
sqlite3 data/waterways.db < sql/queries.sql
```

### 4. Explore the notebook

```bash
jupyter notebook notebooks/analysis.ipynb
```

### 5. Docker

```bash
cd docker
docker compose up --build      # pipeline service runs ETL, db service prints table counts
```

To run the image on its own (default command = ingestion):

```bash
docker build -f docker/Dockerfile -t inland-waterways .
docker run --rm -v "$(pwd)/data:/app/data" inland-waterways
```

### 6. Airflow

```bash
export AIRFLOW_HOME=~/airflow
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
export PIPELINE_HOME=$(pwd)        # lets the DAG locate src/ and data/
airflow dags test inland_waterways_pipeline 2026-01-01
```

The `generate_report` task writes `data/reports/pipeline_report_<date>.txt` with table counts and the Africa-vs-USA utilization gap.

### 7. CI

`.github/workflows/ci.yml` runs on every push and pull request. It runs flake8 on `src/`, `py_compile` on every Python file, and `python src/ingestion/ingest.py --dry-run`.

---

## Key Insights

These figures come from the current data and the queries in `sql/queries.sql`:

- **About a 16x utilization gap.** Over 2015-2022 the US moved about **5,412 tons per navigable km** each year. The African sample (Nigeria, DR Congo, Egypt, Ghana, Mali) moved about **337 tons per km** (Q7).
- **Biggest network, lowest intensity.** DR Congo has 15,000 navigable km, the largest in Africa and denser per land area than the US (6.6 vs 4.5 km per 1,000 sq km). Yet it carries only about **150 tons per km**, versus about 1,243 on Egypt's Nile and about 6,030 in the US in 2023 (Q5).
- **Small volumes.** The five African corridors moved about **9.05 Mt** in 2022, roughly 5% of the Mississippi River System alone (185.5 Mt in 2023).
- **Growth is fragile but positive.** From 2015 to 2022, Ghana's Volta Lake freight grew 36%, Egypt's Nile 24% and Nigeria's Niger 16%. Every corridor fell 10-17% in 2020 (Q3, Q6).
- **The US commodity mix is shifting.** Mississippi coal tonnage fell about 48% from 2010 to 2023, while petroleum rose about 57% (Q2).
- **Rivers alone don't drive trade.** Container TEU correlates strongly with GDP (Spearman ρ ≈ 0.86) and liner connectivity (ρ ≈ 0.82), but only weakly with navigable km (ρ ≈ 0.29). Waterways need links to ports and logistics before they generate freight.
- **African rivers mainly carry people.** They move 2.4-4.0 thousand passengers per thousand tons of freight. The Nile (14.5M passengers) and the Congo (8.9M) are vital social corridors (Q8).

---

## Technologies

- **Python 3.11:** pandas, numpy, requests, PyYAML
- **Apache Airflow 2.8:** weekly orchestration with PythonOperators
- **SQLite:** lightweight analytical warehouse with window-function SQL
- **Docker / Docker Compose:** reproducible containerised runs
- **Jupyter:** interactive EDA and storytelling
- **matplotlib, seaborn, plotly:** static and interactive visualisation
- **GitHub Actions + flake8:** continuous integration

---

## License and Attribution

The data is © the respective providers (World Bank CC BY 4.0, USACE public domain, AfDB, UNCTAD, UN DESA). The pipeline code is provided for educational and analytical purposes.
