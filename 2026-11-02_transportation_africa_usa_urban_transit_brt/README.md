# Urban Transit & BRT Systems: Africa–USA Data Engineering Pipeline

**Domain:** Global Transportation
**Geographic focus:** Africa and the USA

## Problem Statement

Africa's cities are urbanizing fast (about 3.5% urban population growth per year), and much of the USA's transit infrastructure is aging. Both create urgent demand for efficient, data-driven public transit. Bus Rapid Transit (BRT) systems cost much less than rail. This project builds an end-to-end ETL pipeline to analyze three things across Africa and the USA:

- **BRT system performance:** ridership, corridor length, stations and fleet
- **Urban growth drivers:** urban population, growth rate and urbanization share
- **Transit investment patterns:** how national wealth relates to BRT adoption and usage

## Data Sources (real and public)

| Dataset | Source | URL |
|---|---|---|
| BRT systems (20 systems, Africa & USA) | ITDP Global BRT Data | https://brtdata.org/ |
| Urban population (SP.URB.TOTL) | World Bank WDI API | https://api.worldbank.org/v2/country/all/indicator/SP.URB.TOTL?format=json&per_page=500&mrv=5 |
| Urban population growth, annual % (SP.URB.GROW) | World Bank WDI API | https://api.worldbank.org/v2/country/all/indicator/SP.URB.GROW?format=json&per_page=500&mrv=5 |
| GDP per capita, current US$ (NY.GDP.PCAP.CD) | World Bank WDI API | https://api.worldbank.org/v2/country/all/indicator/NY.GDP.PCAP.CD?format=json&per_page=500&mrv=5 |
| Urban population, % of total (SP.URB.TOTL.IN.ZS) | World Bank WDI API | https://api.worldbank.org/v2/country/all/indicator/SP.URB.TOTL.IN.ZS?format=json&per_page=500&mrv=5 |

The ingestion stage pages through every World Bank response. It keeps the 54 African countries plus the USA and drops regional aggregates. The latest run returned 275 country-years (55 countries × 2021–2025) for each indicator.

## Architecture

```
 ┌──────────────────────────────┐
 │       Raw Data Sources       │
 │  ITDP BRT CSV (brtdata.org)  │
 │  World Bank API x4 indicators│
 └──────────────┬───────────────┘
                │  requests + retry (3x, exponential backoff)
                ▼
 ┌──────────────────────────────┐
 │  Ingestion  (ingest.py)      │
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │  data/raw/                   │  timestamped CSV snapshots
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │  Transformation (transform.py)│  clean, cast, dedupe, merge on ISO3,
 └──────────────┬───────────────┘  ridership_per_km / _per_station / fleet ratio
                ▼
 ┌──────────────────────────────┐
 │  data/processed/             │  brt_systems_clean, urban_indicators_clean,
 └──────────────┬───────────────┘  brt_enriched (.csv)
                ▼
 ┌──────────────────────────────┐
 │  Loading  (load.py)          │
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │  SQLite DB                   │  data/transit_analytics.db
 │  brt_systems | urban_indicators | brt_enriched
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │  Analysis                    │  notebooks/analysis.ipynb + sql/queries.sql
 └──────────────┬───────────────┘
                ▼
 ┌──────────────────────────────┐
 │  Insights                    │  charts, rankings, pipeline report
 └──────────────────────────────┘

 Orchestration: Apache Airflow (dags/pipeline_dag.py, @weekly)
 Packaging:     Docker / docker-compose      CI: GitHub Actions (flake8 + smoke test)
```

## Project Structure

```
2026-11-02_transportation_africa_usa_urban_transit_brt/
├── README.md
├── requirements.txt
├── config/config.yaml              # API endpoints, paths, pipeline & logging settings
├── data/
│   ├── raw/                        # source CSVs + timestamped ingestion snapshots
│   ├── processed/                  # cleaned & enriched CSVs
│   └── transit_analytics.db        # SQLite analytics database
├── src/
│   ├── ingestion/ingest.py         # fetch_world_bank_indicator, fetch_brt_systems_data, run_ingestion
│   ├── transformation/transform.py # clean / merge / enrich
│   └── loading/load.py             # schema + loads into SQLite
├── sql/queries.sql                 # 8 analytical queries
├── notebooks/analysis.ipynb        # EDA and visualizations
├── dags/pipeline_dag.py            # Airflow DAG: ingest → transform → load → report
├── docker/Dockerfile
├── docker/docker-compose.yml
└── .github/workflows/ci.yml
```

## How to Run

### 1. Set up the environment
```bash
cd 2026-11-02_transportation_africa_usa_urban_transit_brt
python3.11 -m venv .venv && source .venv/bin/activate
grep -v "^apache-airflow" requirements.txt > requirements-pipeline.txt
pip install -r requirements-pipeline.txt
```
> `apache-airflow==2.8.0` requires `SQLAlchemy<2.0`, which conflicts with the pinned `SQLAlchemy==2.0.23`. Install Airflow in its own environment using the official constraints file (see step 5).

### 2. Run the pipeline stage by stage
```bash
python src/ingestion/ingest.py           # -> data/raw/*_<timestamp>.csv
python src/transformation/transform.py   # -> data/processed/*.csv
python src/loading/load.py               # -> data/transit_analytics.db
```
Optional environment overrides: `DATA_DIR` (parent of raw/ and processed/), `DB_PATH` and `CONFIG_PATH`.

### 3. Explore the results
```bash
sqlite3 data/transit_analytics.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker
```bash
docker compose -f docker/docker-compose.yml up --build
docker compose -f docker/docker-compose.yml exec db sqlite3 /data/transit_analytics.db ".tables"
```

### 5. Orchestrate with Airflow
```bash
pip install "apache-airflow==2.8.0" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.8.0/constraints-3.11.txt"
export AIRFLOW__CORE__DAGS_FOLDER=$(pwd)/dags
airflow standalone          # then trigger DAG "urban_transit_brt_pipeline"
```
The final `generate_report` task writes `reports/pipeline_report.md`.

## Key Insights

All figures below come from the pipeline outputs (`sql/queries.sql` run against `data/transit_analytics.db`).

1. **African corridors are the busiest per km.** Lagos BRT-Lite serves **200,000** riders/day on 22 km (about 9,090 riders/km). Dar es Salaam DART serves **180,000** riders/day on 20.9 km (about 8,610/km). African systems average **3,740 riders/km**, versus **1,197** for US systems.
2. **The USA leads on scale, not intensity.** NYC Select Bus Service (400,000/day) and LA Metro Rapid (350,000/day) top the ridership ranking, but they run on 320 km and 580 km networks. The 10 US systems cover 1,112 corridor-km in total; the 10 African systems cover 258 km.
3. **African fleets are heavily used.** DART (1,286 riders per vehicle) and Addis Ababa BRT (1,091) lead the sample. The African average is 718 riders per vehicle, versus 309 in the USA, which points to capacity pressure and a case for expanding fleets.
4. **Station productivity:** Lagos (8,333 riders/station) and DART (6,207) rank first and second. Pittsburgh's East Busway (2,083) is the most productive US system.
5. **Urban growth keeps pushing demand up.** African urban population grew about **3.3% per year** on average (54 countries, 2021–2025), compared with about 0.6% for the USA in 2025. Tanzania (4.8%), Uganda (4.8%) and Ethiopia (4.6%), all BRT countries, are among the fastest-urbanizing.
6. **Wealth does not predict ridership.** Ridership per km is negatively correlated with GDP per capita (r ≈ −0.55). African BRT countries average about US$2,850 GDP per capita, against about US$90,000 for the USA.
7. **Africa's BRT wave is recent.** 5 of the 10 African systems opened in 2022–2024. The median opening year is 2019 for African systems and 2008 for US systems.
8. **BRT adoption vs urbanization:** the 10 countries with BRT and the 45 without have similar urban growth rates (3.19% vs 3.20%). The BRT group's average GDP per capita (US$11,193, raised by the USA) is much higher than the non-BRT group's (US$3,242).

## Technologies

Python 3.11 · pandas · NumPy · requests · SQLite · Apache Airflow · Docker / docker-compose · Jupyter · matplotlib · seaborn · plotly · PyYAML · flake8 · GitHub Actions
