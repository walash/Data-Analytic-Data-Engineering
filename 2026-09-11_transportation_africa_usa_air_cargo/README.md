# Africa-USA Air Cargo & Freight Routes: A Data Engineering Analysis

**Domain:** Global Transportation
**Geographic focus:** Africa & USA

## Problem Statement

Air cargo is the backbone of high-value, time-sensitive trade between Africa and
the United States, yet the connectivity between African airports and US hubs is
poorly understood at the network level. This project builds an end-to-end data
engineering pipeline that ingests open aviation and economic datasets, models the
Africa <-> USA route network, and quantifies national air-freight and
air-passenger performance across the two regions.

The pipeline answers questions such as:

- Which African airports and US hubs anchor the transatlantic cargo network?
- Which airlines operate direct Africa <-> USA services, and in which direction?
- How large is each African country's air-freight volume, and how fast is it
  growing year over year?
- How do African and US air-freight and passenger metrics compare?
- Which African countries are the most freight-intensive relative to passengers?

## Data Sources

All sources are public and free (no API keys required):

| Source | Description | URL |
| ------ | ----------- | --- |
| OurAirports | Global airport reference data (86k+ airports) | https://davidmegginson.github.io/ourairports-data/airports.csv |
| OpenFlights (routes) | Global airline route network (67k+ routes) | https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat |
| OpenFlights (airports) | Airport reference with IATA/country mapping | https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat |
| World Bank (freight) | Air transport freight, million ton-km (IS.AIR.GOOD.MT.K1) | https://api.worldbank.org/v2/country/all/indicator/IS.AIR.GOOD.MT.K1?format=json |
| World Bank (passengers) | Air transport passengers carried (IS.AIR.PSGR) | https://api.worldbank.org/v2/country/all/indicator/IS.AIR.PSGR?format=json |

## Architecture

```
                     +--------------------------------------------+
                     |              DATA SOURCES                  |
                     |  OurAirports | OpenFlights | World Bank API|
                     +----------------------+---------------------+
                                            |
                                            v
   +----------------------------------------------------------------------+
   |  INGESTION  (src/ingestion/ingest.py)                                 |
   |  - HTTP fetch with tenacity retry + logging                           |
   |  - writes raw snapshots  ------------------------------> data/raw/    |
   +----------------------------------------+-----------------------------+
                                            |
                                            v
   +----------------------------------------------------------------------+
   |  TRANSFORMATION  (src/transformation/transform.py)                    |
   |  - filter African (continent=AF) & US (iso_country=US) airports       |
   |  - parse header-less OpenFlights routes.dat                           |
   |  - classify Africa<->USA routes via IATA lookups                      |
   |  - parse World Bank JSON -> tidy long tables                          |
   |  - clean nulls, cast types, dedupe -----------------> data/processed/ |
   +----------------------------------------+-----------------------------+
                                            |
                                            v
   +----------------------------------------------------------------------+
   |  LOADING  (src/loading/load.py)                                       |
   |  - create SQLite schema + indexes                                     |
   |  - load airports / routes / freight / passenger tables               |
   |                                          --------> data/air_cargo.db  |
   +----------------------------------------+-----------------------------+
                                            |
                    +-----------------------+-----------------------+
                    v                                               v
        +-----------------------+                     +--------------------------+
        |  ANALYSIS             |                     |  SQL QUERIES             |
        |  notebooks/           |                     |  sql/queries.sql         |
        |  analysis.ipynb       |                     |  (10 analytical queries) |
        +-----------------------+                     +--------------------------+

   Orchestration: dags/pipeline_dag.py (Airflow, @weekly)
   Packaging:     docker/Dockerfile + docker/docker-compose.yml
   CI:            .github/workflows/ci.yml (flake8 lint + ingest dry-run)
```

## How to Run

### 1. Set up the environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
# Ingest raw data into data/raw/
python src/ingestion/ingest.py

# Transform into analysis-ready CSVs in data/processed/
python src/transformation/transform.py

# Load into the SQLite database data/air_cargo.db
python src/loading/load.py
```

### 3. Explore the results

```bash
# Run the SQL queries
sqlite3 data/air_cargo.db < sql/queries.sql

# Open the analysis notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder. The DAG
`africa_usa_air_cargo_pipeline` runs weekly and chains
`ingest_data -> transform_data -> load_data -> generate_report`.

## Key Insights

The analysis notebook and SQL queries surface findings such as:

- **US gateways:** A small number of US hubs (e.g. New York JFK, Washington
  Dulles, Atlanta) concentrate the majority of direct Africa <-> USA services.
- **African anchors:** North and Southern African hubs (e.g. Cairo, Johannesburg,
  Addis Ababa, Casablanca) dominate both scheduled service and international
  connectivity from the continent.
- **Freight leaders:** A handful of African economies account for a
  disproportionate share of the continent's air-freight ton-km, and their
  year-over-year growth is uneven.
- **Africa vs USA scale:** US aggregate air-freight and passenger volumes dwarf
  the African total, underscoring the connectivity gap the network faces.
- **Freight intensity:** Some African countries carry far more freight per
  passenger than others, revealing cargo-oriented versus passenger-oriented
  aviation markets.

## Technologies Used

- **Python 3.11** -- ingestion, transformation, and loading logic
- **pandas / numpy** -- data wrangling and cleaning
- **requests / tenacity** -- resilient HTTP downloads with retry/backoff
- **SQLite / SQLAlchemy** -- analytical data store
- **matplotlib / seaborn / plotly** -- visualization in the notebook
- **Apache Airflow** -- weekly pipeline orchestration
- **Docker / docker-compose** -- reproducible packaging
- **GitHub Actions (flake8)** -- continuous integration and linting
- **PyYAML** -- externalized configuration

## Project Structure

```
.
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
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
