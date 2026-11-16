# Deepwater Offshore Oil Production: Africa vs USA Gulf of Mexico

| | |
|---|---|
| **Domain** | Oil and Gas |
| **Geographic focus** | Africa (Gulf of Guinea / West-Central Africa) & USA (Gulf of Mexico) |
| **Period** | 2010 – 2023 |
| **Pipeline** | Python ETL → SQLite → SQL analytics + Jupyter EDA, orchestrated by Airflow, packaged with Docker, tested in GitHub Actions |

## Problem Statement

Deepwater offshore basins are where Africa and the United States compete most directly for upstream investment.
West and Central Africa (Nigeria's Niger Delta, Angola's Lower Congo and Kwanza basins, Equatorial Guinea, Congo,
Gabon, Cameroon and Ghana's Jubilee/TEN fields) and the US Gulf of Mexico all rely on high-cost, long-lead-time
deepwater projects. This project builds a reproducible data pipeline to answer:

1. How have oil production trends of the main African deepwater producers diverged from the USA since 2010?
2. Which countries have passed peak production, and how steep is the decline from peak?
3. How dependent is each economy on oil (oil rents as % of GDP), and how did the 2014-16 and 2020 price shocks hit them?
4. How does oil production relate to GDP and population in each country?

> **Scope note:** the public sources publish **national** totals. The USA series therefore includes onshore shale
> as well as Gulf of Mexico deepwater output and is used as the GoM proxy; the African producers' output is
> predominantly offshore. Basin-level volumes (e.g. BSEE, EIA) can be added via the API-key slots in `config/config.yaml`.

## Data Sources (real, public, no key required)

| Source | URL | Used for |
|---|---|---|
| Our World in Data – Energy dataset (CSV) | https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv | oil production (TWh), per-capita production, GDP, population |
| OWID energy-data repository / codebook | https://github.com/owid/energy-data | column definitions |
| World Bank Indicators API – Oil rents (% of GDP) | https://api.worldbank.org/v2/country/{iso}/indicator/NY.GDP.PETR.RT.ZS?format=json | oil dependence |

Countries: Nigeria (NGA), Angola (AGO), Equatorial Guinea (GNQ), Congo Rep. (COG), Gabon (GAB), Cameroon (CMR),
Ghana (GHA), United States (USA). Raw snapshots are stored in `data/raw/`:
`owid_oil_production.csv`, `wb_oil_rents_raw.json` and the merged `oil_production_merged.csv` (112 rows = 8 countries × 14 years).

## Architecture

```
 ┌────────────────────────────┐   ┌──────────────────────────────────┐
 │ OWID energy CSV (GitHub)   │   │ World Bank API NY.GDP.PETR.RT.ZS │
 └─────────────┬──────────────┘   └────────────────┬─────────────────┘
               │  HTTP + tenacity retries          │ batched -> per-country -> cached fallback
               ▼                                   ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                                  │
 │    filter 8 countries, 2010-2023 -> merge on iso_code + year         │
 │    data/raw/{owid_oil_production.csv, wb_oil_rents_raw.json,         │
 │              oil_production_merged.csv}                              │
 └───────────────────────────────┬─────────────────────────────────────┘
                                 ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM   src/transformation/transform.py                       │
 │    clean nulls, cast types, dedupe, region (Africa/USA), basin,      │
 │    kbd conversions, YoY %, cumulative TWh, regional share, rents USD │
 │    data/processed/{oil_production_processed.csv,                     │
 │                    oil_rents_processed.csv}                          │
 └───────────────────────────────┬─────────────────────────────────────┘
                                 ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py  ->  SQLite data/oil_production.db     │
 │    tables: oil_production, oil_rents (PK iso_code, year) + indexes   │
 └───────────────┬───────────────────────────────┬─────────────────────┘
                 ▼                               ▼
 ┌───────────────────────────────┐   ┌───────────────────────────────────┐
 │ 4a. sql/queries.sql (8 queries)│   │ 4b. notebooks/analysis.ipynb (EDA)│
 └───────────────────────────────┘   └───────────────────────────────────┘
                 ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ 5. REPORT  data/reports/pipeline_report_<date>.txt                   │
 └─────────────────────────────────────────────────────────────────────┘

 Orchestration: Airflow DAG `deepwater_oil_africa_usa_pipeline` (Mon/Wed/Fri 06:00)
   ingest_data >> transform_data >> load_data >> generate_report
 Packaging: docker/Dockerfile + docker/docker-compose.yml   CI: .github/workflows/ci.yml
```

## How to Run

All commands are run from this project folder.

```bash
# 1. Environment
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Pipeline stage by stage
python src/ingestion/ingest.py            # downloads OWID + World Bank (use --dry-run to validate config only)
python src/transformation/transform.py    # -> data/processed/*.csv
python src/loading/load.py                # -> data/oil_production.db

# or everything incl. the report, without Airflow:
python dags/pipeline_dag.py               # add --skip-ingest to reuse data/raw/

# 3. Analytics
sqlite3 -header -column data/oil_production.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

**Docker**

```bash
cd docker
docker compose up --build                 # runs ingest -> transform -> load -> report
docker compose run --rm sqlite            # optional SQLite shell on the shared volume (profile "tools")
```

**Airflow** (separate environment, because Airflow 2.8.1 pins SQLAlchemy < 2.0):

```bash
pip install "apache-airflow==2.8.1" --constraint \
  "https://raw.githubusercontent.com/apache/airflow/constraints-2.8.1/constraints-3.11.txt"
pip install pandas==2.1.4 requests==2.31.0 tenacity==8.2.3 pyyaml==6.0.1
export PROJECT_ROOT=$(pwd) AIRFLOW__CORE__DAGS_FOLDER=$(pwd)/dags
airflow standalone
```

Environment overrides: `CONFIG_PATH`, `DATA_RAW_DIR`, `DATA_PROCESSED_DIR`, `DB_PATH`, `LOG_LEVEL`, `PROJECT_ROOT`.

## Derived Metrics

| Column | Definition |
|---|---|
| `oil_production_kbd` | `oil_production_twh * 1000 / 8.76 / 24` – the project's specified TWh/yr → kbd conversion |
| `oil_production_kbd_bbl` | physical cross-check: `twh * 1e6 / 1.7 MWh per bbl / 365 / 1000` (thousand barrels/day) |
| `yoy_change_pct`, `cumulative_twh` | per-country year-over-year change and running total |
| `share_of_region_pct` | country share of its region's (Africa or USA) annual output |
| `oil_rents_usd` | `gdp * oil_rents_pct_gdp / 100` |

The two kbd columns differ by design: the specified formula is a fixed scaling of TWh, while the barrel-based figure
(e.g. Angola 2010 ≈ 1,667 kbd, Nigeria 2010 ≈ 2,287 kbd) is directly comparable with industry-reported volumes.

## Key Insights (from the 2010–2023 data in this repo)

1. **Sharp divergence.** US output rose from 3,874 TWh (2010) to 9,648 TWh (2023), **+149%**. The five African producers
   with complete series (Nigeria, Angola, Equatorial Guinea, Congo, Gabon) fell from 2,943 TWh to 1,851 TWh, **−37%**.
2. **Scale gap widened ~4×.** The USA:Africa(core 5) production ratio went from **1.3×** in 2010 to **5.2×** in 2023.
3. **Africa peaked early.** Nigeria (peak 1,419 TWh in 2010, −39% by 2023), Angola (peak 2010, −37%) and Equatorial
   Guinea (peak 176 TWh in 2012, −73% by 2023) are in post-plateau deepwater decline; Congo (−11%) and Gabon (−4%) held up.
   The USA peaked in the latest year, 2023.
4. **Concentration.** Nigeria and Angola together supply ~80% of the African group's output in every year.
5. **Extreme fiscal exposure.** Oil rents reached **48.7% of GDP in Congo (2011)** and 30–39% in Angola, Gabon and Equatorial
   Guinea, collapsing by roughly two-thirds in the 2015 price crash (Angola 38.6% → 12.7%). US oil rents never exceed 0.6% of GDP.
6. **Production vs GDP.** GDP is strongly positively correlated with output in the USA (r ≈ 0.97) and Equatorial Guinea
   (r ≈ 0.96), but negatively in Nigeria (≈ −0.88), Gabon (≈ −0.76) and Congo (≈ −0.62) – growth there is decoupling
   from crude volumes.

*Data limitations:* OWID production for Cameroon and Ghana is missing after 2016; World Bank oil rents end in 2021;
OWID consumption columns are largely empty for African producers.

## Repository Layout

```
2026-11-16_oil_gas_africa_usa_deepwater_offshore/
├── README.md
├── requirements.txt
├── config/config.yaml
├── data/
│   ├── raw/            oil_production_merged.csv, owid_oil_production.csv, wb_oil_rents_raw.json
│   ├── processed/      generated by transform.py
│   └── reports/        generated by the report task
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/queries.sql
├── notebooks/analysis.ipynb
├── dags/pipeline_dag.py
├── docker/{Dockerfile, docker-compose.yml}
└── .github/workflows/ci.yml
```

## Technologies Used

Python 3.11 · pandas · requests · tenacity · PyYAML · SQLite · SQL (window functions, CTEs) · matplotlib · seaborn ·
Plotly · Jupyter · Apache Airflow 2.8 · Docker / Docker Compose · GitHub Actions · flake8 · pytest
