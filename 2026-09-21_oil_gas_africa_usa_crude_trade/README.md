# Oil & Gas Data Engineering: Africa–USA Crude Trade & Production Analysis

**Domain:** Oil and Gas  |  **Geographic Focus:** Africa (11 major oil-producing nations) + USA

An end-to-end data engineering pipeline that ingests real World Bank development
indicators, transforms them into an analysis-ready master table, loads them into
a SQLite warehouse, and surfaces insights through SQL queries and a Jupyter
analysis notebook.

## Problem Statement

Petroleum shapes the economies of Africa's leading oil producers very
differently than it does the diversified economy of the USA. This project
analyzes **oil dependency, energy use, foreign direct investment (FDI) flows,
and the broader economic impact of petroleum** across Africa's top oil producers
versus the United States. It answers questions such as:

- Which African economies are most dependent on oil rents as a share of GDP?
- How volatile are oil rents year over year for producers like Nigeria and Angola?
- Do highly oil-dependent countries tend to have lower GDP per capita?
- How does per-capita energy use in Africa compare to the USA?
- Where do FDI inflows and merchandise exports concentrate?

## Countries Analyzed

Nigeria (NGA), Angola (AGO), Libya (LBY), Algeria (DZA), Gabon (GAB),
Congo, Rep. (COG), Equatorial Guinea (GNQ), Chad (TCD), Cameroon (CMR),
Ghana (GHA), Egypt (EGY), and the United States (USA).

## Real Data Sources (World Bank API v2)

All data is fetched live from the World Bank Indicators API (most recent 10
years, `mrv=10`):

| Indicator | Code | URL |
|-----------|------|-----|
| Oil rents (% of GDP) | `NY.GDP.PETR.RT.ZS` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/NY.GDP.PETR.RT.ZS?format=json&per_page=500&mrv=10 |
| Total natural resources rents (% of GDP) | `NY.GDP.TOTL.RT.ZS` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/NY.GDP.TOTL.RT.ZS?format=json&per_page=500&mrv=10 |
| GDP per capita (current US$) | `NY.GDP.PCAP.CD` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/NY.GDP.PCAP.CD?format=json&per_page=500&mrv=10 |
| Energy use (kg oil equivalent per capita) | `EG.USE.PCAP.KG.OE` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/EG.USE.PCAP.KG.OE?format=json&per_page=500&mrv=10 |
| Electricity from oil sources (% of total) | `EG.ELC.PETR.ZS` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/EG.ELC.PETR.ZS?format=json&per_page=500&mrv=10 |
| Merchandise exports (current US$) | `TX.VAL.MRCH.CD.WT` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/TX.VAL.MRCH.CD.WT?format=json&per_page=500&mrv=10 |
| FDI net inflows (BoP, current US$) | `BX.KLT.DINV.CD.WD` | https://api.worldbank.org/v2/country/NGA;AGO;LBY;DZA;GAB;COG;GNQ;TCD;CMR;GHA;EGY;USA/indicator/BX.KLT.DINV.CD.WD?format=json&per_page=500&mrv=10 |

## Architecture

```
   ┌─────────────────────────┐
   │  World Bank REST APIs    │   (7 indicators × 12 countries, JSON)
   │  api.worldbank.org/v2    │
   └───────────┬─────────────┘
               │  HTTP GET (retry + backoff)
               ▼
   ┌─────────────────────────┐
   │  Ingestion  ingest.py    │
   └───────────┬─────────────┘
               │  raw JSON
               ▼
   ┌─────────────────────────┐
   │  data/raw/*.json         │
   └───────────┬─────────────┘
               │  parse / clean / merge
               ▼
   ┌─────────────────────────┐
   │ Transformation transform.py │
   └───────────┬─────────────┘
               │  wide master table
               ▼
   ┌─────────────────────────────────────────┐
   │ data/processed/oil_gas_master.csv|.parquet│
   └───────────┬─────────────────────────────┘
               │  load (pandas to_sql)
               ▼
   ┌─────────────────────────┐
   │  Loading  load.py        │
   └───────────┬─────────────┘
               │  SQLite
               ▼
   ┌─────────────────────────┐
   │  data/oil_gas.db         │
   │  table: oil_gas_metrics  │
   └───────────┬─────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌──────────────┐  ┌──────────────────┐
│ SQL Queries  │  │ Analysis Notebook│
│ sql/queries  │  │ notebooks/*.ipynb│
└──────────────┘  └──────────────────┘

  Orchestration: Apache Airflow (dags/pipeline_dag.py)
  Packaging:     Docker + docker-compose (docker/)
  CI:            GitHub Actions (.github/workflows/ci.yml)
```

## Project Structure

```
2026-09-21_oil_gas_africa_usa_crude_trade/
├── README.md
├── requirements.txt
├── config/config.yaml
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/queries.sql
├── notebooks/analysis.ipynb
├── dags/pipeline_dag.py
├── docker/{Dockerfile,docker-compose.yml}
├── .github/workflows/ci.yml
└── data/{raw,processed}/
```

## How to Run

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
# Ingest raw data from the World Bank API into data/raw/
python src/ingestion/ingest.py

# Transform raw JSON into the wide-format master table
python src/transformation/transform.py

# Load the master table into data/oil_gas.db
python src/loading/load.py
```

### 3. Explore the data

```bash
# Run the SQL analytics
sqlite3 data/oil_gas.db < sql/queries.sql

# Or open the notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
docker compose -f docker/docker-compose.yml up --build
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder (or point
`AIRFLOW__CORE__DAGS_FOLDER` at this project's `dags/`), then enable the
`oil_gas_africa_usa_pipeline` DAG. It runs weekly:
`ingest_data → transform_data → load_data → generate_report`.

## Key Insights

- **Oil rents dominate several African economies.** Libya, Congo, and Angola
  routinely derive well over 20% of GDP from oil rents, versus a negligible
  share for the diversified USA economy.
- **Oil dependency correlates with lower income.** Highly oil-dependent
  producers frequently post GDP per capita below US$5,000, illustrating the
  classic "resource curse" pattern relative to the USA.
- **Oil rents are highly volatile.** Year-over-year swings for Nigeria and
  Angola track global crude price cycles, exposing these economies to external
  shocks.
- **Energy use per capita is an order of magnitude higher in the USA** than the
  African average, reflecting deep differences in industrialization and living
  standards despite Africa's role as a crude supplier.
- **FDI and merchandise exports concentrate in the largest producers.** Nigeria,
  Angola, Egypt, and the USA capture the bulk of FDI inflows and export value,
  underscoring how petroleum shapes capital and trade flows.

## Technologies

Python 3.10 · pandas · SQLite · Apache Airflow · Docker · Jupyter ·
matplotlib / seaborn / plotly · pyarrow · PyYAML · flake8 · GitHub Actions
