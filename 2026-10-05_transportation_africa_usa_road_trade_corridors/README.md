# Africa-USA Road Infrastructure & Cross-Border Trade Corridors Analytics

**Domain:** Global Transportation
**Geographic focus:** 15 African countries — Nigeria, Ghana, Kenya, Ethiopia, South Africa, Egypt, Morocco, Tanzania, Uganda, Senegal, Cameroon, Côte d'Ivoire, Zambia, Mozambique, Rwanda — benchmarked against the **United States**
**Pipeline:** World Bank API → Python ETL → SQLite warehouse → SQL analytics & Jupyter visualisations, orchestrated by Airflow, containerised with Docker, validated by GitHub Actions

---

## Problem Statement

Africa's cross-border trade corridors — the Northern Corridor (Mombasa–Kampala–Kigali), the Abidjan–Lagos coastal corridor, the Dar es Salaam Central Corridor, the Maputo Development Corridor and the North-South Corridor through Zambia and South Africa — move most of the continent's goods by road. Under the African Continental Free Trade Area (AfCFTA), the efficiency of those corridors determines whether lower tariffs actually translate into more trade.

Yet road networks remain sparse and largely unpaved, and logistics performance trails global benchmarks. This project builds a reproducible data pipeline that quantifies:

1. **The logistics gap** — how far each African economy's Logistics Performance Index (LPI) and LPI infrastructure sub-score sit below the USA.
2. **The physical network** — road density (km per 100 km² of land) and the share of paved roads.
3. **Trade intensity** — merchandise exports and trade as a share of GDP, and whether they move with road infrastructure.
4. **Trends** — how LPI scores evolved across the 2012, 2014, 2016, 2018 and 2023 editions.

## Data Sources

All data comes from the free, public **World Bank Indicators API v2** (no API key required). `{countries}` = `NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZM;MZ;RW;USA`.

| Indicator | Code | URL |
|---|---|---|
| Logistics Performance Index — overall (1–5) | `LP.LPI.OVRL.XQ` | https://api.worldbank.org/v2/country/{countries}/indicator/LP.LPI.OVRL.XQ?format=json&per_page=200&mrv=5 |
| LPI — infrastructure sub-score (1–5) | `LP.LPI.INFR.XQ` | https://api.worldbank.org/v2/country/{countries}/indicator/LP.LPI.INFR.XQ?format=json&per_page=200&mrv=5 |
| Road density (km per 100 km² land) | `IS.ROD.DNST.K2` | https://api.worldbank.org/v2/country/{countries}/indicator/IS.ROD.DNST.K2?format=json&per_page=200&mrv=10 |
| Paved roads (% of total roads) | `IS.ROD.PAVE.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/IS.ROD.PAVE.ZS?format=json&per_page=200&mrv=10 |
| Merchandise exports (current USD) | `TX.VAL.MRCH.CD.WT` | https://api.worldbank.org/v2/country/{countries}/indicator/TX.VAL.MRCH.CD.WT?format=json&per_page=200&mrv=5 |
| Trade (% of GDP) | `NE.TRD.GNFS.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/NE.TRD.GNFS.ZS?format=json&per_page=200&mrv=5 |
| GDP per capita (current USD) | `NY.GDP.PCAP.CD` | https://api.worldbank.org/v2/country/{countries}/indicator/NY.GDP.PCAP.CD?format=json&per_page=200&mrv=5 |

**Data notes**

- The World Bank files the **2023 LPI edition under year 2022**; LPI editions in the data are 2012, 2014, 2016, 2018 and 2022 (=2023).
- Road density and paved-roads are legacy series last updated between **2001 and 2010**, with no values for the USA. They are used as structural indicators of the network.
- Legacy road series return an empty `countryiso3code`; the pipeline recovers ISO3 codes from country names.
- Nigeria has no trade-%-of-GDP observation in the extract.

Raw extract sizes: LPI overall 63 rows, LPI infrastructure 63, road density 47, paved roads 49, merchandise exports 80, trade % GDP 72, GDP per capita 80.

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                 World Bank Indicators API v2 (public, JSON)              │
 │  LPI overall · LPI infrastructure · road density · paved roads ·         │
 │  merchandise exports · trade % GDP · GDP per capita   (16 countries)     │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     │ HTTPS, 3 retries, exponential backoff
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                                      │
 │    data/raw/<indicator>.json  +  data/raw/<indicator>.csv (tidy, long)   │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM   src/transformation/transform.py                           │
 │    type casting · ISO3 repair · dedup · pivot country-year ·             │
 │    forward-fill · drop rows >50% missing · LPI gap vs USA ·              │
 │    trade-openness category                                               │
 │    → data/processed/master_transport_trade.csv                           │
 │    → data/processed/country_summary.csv                                  │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py  →  data/transport_trade.db (SQLite)       │
 │    raw_indicators · master_transport_trade · country_summary ·           │
 │    lpi_rankings   + 6 indexes                                            │
 └──────────────┬──────────────────────────────────────┬────────────────────┘
                ▼                                      ▼
 ┌──────────────────────────────┐      ┌───────────────────────────────────┐
 │ 4a. sql/queries.sql          │      │ 4b. notebooks/analysis.ipynb      │
 │     8 analytical queries     │      │     6 charts + key findings       │
 └──────────────────────────────┘      └───────────────────────────────────┘

 Orchestration: dags/pipeline_dag.py (Airflow, @weekly)
   ingest_data → transform_data → load_to_db → generate_report
 Packaging: docker/Dockerfile + docker/docker-compose.yml
 CI: .github/workflows/ci.yml (flake8 + ingestion dry-run + transform/load smoke test)
```

## Project Structure

```
2026-10-05_transportation_africa_usa_road_trade_corridors/
├── README.md
├── requirements.txt
├── config/config.yaml              # API, countries, indicators, paths, pipeline params
├── data/
│   ├── raw/                        # JSON + CSV per indicator (World Bank)
│   ├── processed/                  # master_transport_trade.csv, country_summary.csv
│   └── transport_trade.db          # SQLite warehouse (created by load.py)
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/queries.sql
├── notebooks/analysis.ipynb
├── dags/pipeline_dag.py
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/workflows/ci.yml
```

## How to Run

### 1. Local environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Airflow is heavy; to run only the ETL scripts install `requests pandas numpy pyyaml`.

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run     # validate config and print the 7 API URLs
python src/ingestion/ingest.py               # download JSON + CSV to data/raw/
python src/transformation/transform.py       # build data/processed/*.csv
python src/loading/load.py                   # build data/transport_trade.db
```

### 3. Explore the results

```bash
sqlite3 -header -column data/transport_trade.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker

```bash
cd docker
docker compose up --build        # runs ingest → transform → load; data/ is mounted as a volume
```

or without compose:

```bash
docker build -f docker/Dockerfile -t africa-usa-road-trade:latest .
docker run --rm -v "$(pwd)/data:/app/data" africa-usa-road-trade:latest
```

### 5. Airflow

```bash
export AIRFLOW_HOME=~/airflow
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/   # or point dags_folder at this repo's dags/
airflow dags test africa_usa_road_trade_corridors_pipeline 2026-10-05
```

The DAG `africa_usa_road_trade_corridors_pipeline` runs `@weekly` with 2 retries (5-minute delay) and writes `data/processed/pipeline_report.txt`.

## Key Insights

Computed from the World Bank data in `data/raw/` (see `sql/queries.sql` and `notebooks/analysis.ipynb`):

1. **A persistent logistics gap.** The USA scores **3.80** on the 2023 LPI. **South Africa (3.70)** is the only African economy within 0.1 points, followed by **Egypt (3.10)**. The average of the 15 African countries' latest scores is **2.71 — about 71% of the US level**; **Cameroon (2.10)** trails by 1.70 points.
2. **Infrastructure is the binding constraint.** The average African LPI infrastructure sub-score is **2.54** versus **3.90** for the USA. Mozambique (infrastructure 0.45 below its overall LPI), Uganda (−0.39), Kenya and Ethiopia (≈−0.26) show the largest infrastructure drag.
3. **Most of the network is unpaved.** Only **Egypt (92.2%)** and **Morocco (70.4%)** pave a majority of their roads; the other 13 countries range from **7.9% (Côte d'Ivoire)** to **35.5% (Senegal)**. Rwanda has the densest network (**53 km per 100 km²**); Ethiopia and Mozambique the sparsest (**4 km**).
4. **Road density alone does not explain trade openness.** Across 14 countries the correlation between road density and trade-%-of-GDP is essentially zero (**r = 0.07, r² ≈ 0.005**). Port-led economies such as **Mozambique (97.1%)** and **Morocco (93.1%)** are highly open despite sparse networks, which points to corridor quality, port connectivity and border efficiency mattering more than raw road length.
5. **Exports are concentrated.** The 15 African economies exported **≈$410bn** of merchandise in 2025, about **one-fifth of US exports ($2,185bn)**. **South Africa accounts for 28%** of the African total; South Africa, Nigeria, Egypt and Morocco together account for **two-thirds**.
6. **Gradual convergence.** The mean LPI gap of surveyed African countries versus the USA narrowed from **−1.27 (2012)** to **−1.00 (2023 edition)**. Part of that is a slight decline in the US score (3.93 → 3.80), and the 2023 edition covered only 6 of the 15 African countries.
7. **Openness categories:** 3 countries are **High** (>70% of GDP: Mozambique, Morocco, Senegal), 7 **Moderate**, 4 **Low** (Kenya, Tanzania, Ethiopia, Cameroon). The USA sits at **25.0%**, which is typical for a large domestic market.

## SQLite Schema

| Table | Grain | Purpose |
|---|---|---|
| `raw_indicators` | country × indicator × observed year | Cleaned raw observations (no filling) |
| `master_transport_trade` | country × year | Wide, forward-filled table with LPI gaps and openness category |
| `country_summary` | country | Latest value + observation year per indicator, LPI rank |
| `lpi_rankings` | country × LPI edition | Overall and Africa-only rank per edition |

## Technologies Used

- **Python 3.11**: `requests`, `pandas`, `numpy`, `pyyaml`
- **SQLite**: analytical warehouse with window-function queries
- **Jupyter**, **matplotlib**, **seaborn**, **plotly**: exploratory analysis and visualisation
- **Apache Airflow 2.8**: weekly orchestration
- **Docker / Docker Compose**: containerised pipeline
- **GitHub Actions**: flake8 linting and smoke tests
- **World Bank Indicators API v2**: open data source
