# Africa LNG Exports to the USA: A Data Engineering Pipeline

**Domain:** Oil and Gas
**Geographic focus:** Africa (Nigeria, Algeria, Egypt, Angola, Mozambique, Tanzania, Cameroon, Equatorial Guinea, Libya, Gabon) and the USA
**Period:** 2019–2023 (trade), 2012–2025 (World Bank indicators)

---

## Problem Statement

Africa holds significant natural gas reserves, and LNG exports are a critical revenue source. This project analyzes LNG trade flows from African nations to the USA and global markets, examining trends from 2019–2023, economic dependencies, and the role of gas rents in national GDPs.

Questions answered:

1. Which African countries lead LNG exports, and how did they change between 2019 and 2023?
2. How large — and how fast-growing — are African LNG shipments to the USA?
3. How dependent are African economies on gas rents, gas-fired power and LNG export revenue?
4. Does gas wealth translate into higher GDP per capita?

## Data Sources

| Source | Endpoint | Content |
|--------|----------|---------|
| **UN Comtrade API** (public preview, no key) | `https://comtradeapi.un.org/public/v1/preview/C/A/HS?cmdCode=271111` | Annual HS 271111 (liquefied natural gas) trade by reporter, partner and flow (X export, M import, RX re-export), value in USD and net weight in kg |
| **World Bank Open Data API** | `https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json` | `NY.GDP.NGAS.RT.ZS` gas rents % GDP, `NY.GDP.PETR.RT.ZS` oil rents % GDP, `NY.GDP.TOTL.RT.ZS` total resource rents % GDP, `TX.VAL.MRCH.CD.WT` merchandise exports USD, `EG.ELC.NGAS.ZS` electricity from gas %, `NY.GDP.PCAP.CD` GDP per capita, `SP.POP.TOTL` population |

Bundled raw snapshots: `data/raw/lng_trade_comtrade.csv` (275 rows) and `data/raw/worldbank_energy_indicators.csv` (700 rows).

> **Coverage note.** The bundled Comtrade snapshot (and therefore the analysis, SQL results and notebook) covers 2019, 2020 and 2023 for eight of the ten countries — Algeria and Equatorial Guinea return no HS 271111 records from the preview endpoint, and Libya reports only 2019. The preview API accepts one period per request, so `ingest.py` queries each reporter-year separately; a live run on 2026-10-02 also returned 2021 and 2022 (472 trade rows), and re-running the pipeline will pick those years up. World Bank rents series currently end in 2021, so the processed table carries the latest value forward (max. 3 years) for 2023. Reported quantities (kg) are inconsistent across reporters, so the analysis relies on USD values.

## Architecture

```
 ┌──────────────────────────┐      ┌──────────────────────────────┐
 │ UN Comtrade API          │      │ World Bank Open Data API     │
 │ HS 271111 (LNG), 10 AFR  │      │ 7 energy / macro indicators  │
 │ reporters, 2019-2023     │      │ 10 countries, 2012-2025      │
 └────────────┬─────────────┘      └───────────────┬──────────────┘
              │  requests + tenacity retries       │
              ▼                                    ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                             │
 │    data/raw/lng_trade_comtrade.csv                              │
 │    data/raw/worldbank_energy_indicators.csv                     │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM   src/transformation/transform.py (pandas)         │
 │    clean / type-cast / dedupe → resolve partner names           │
 │    filter exports (flow = X) → country × year pivot             │
 │    pivot WB indicators wide → merge → derived metrics           │
 │    (export_value_billion_usd, usa_share_pct, yoy_growth_pct,    │
 │     lng_share_of_merch_exports_pct, gas_dependency_score)       │
 │    data/processed/*.csv                                         │
 └──────────────────────────────┬──────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py  →  SQLite data/lng_pipeline.db   │
 │    lng_trade_raw · wb_indicators · wb_indicators_wide ·         │
 │    lng_exports_processed   (PK-based upserts, indexes)          │
 └───────────────┬───────────────────────────────┬─────────────────┘
                 ▼                               ▼
   ┌──────────────────────────┐    ┌──────────────────────────────┐
   │ sql/queries.sql          │    │ notebooks/analysis.ipynb     │
   │ 10 analytical queries    │    │ charts + narrative findings  │
   └──────────────────────────┘    └──────────────────────────────┘

 Orchestration: Airflow DAG `lng_exports_pipeline` (@weekly)
   ingest_data → transform_data → load_data → generate_report
 Packaging: Docker / docker-compose     CI: GitHub Actions (flake8 + smoke tests)
```

## Project Structure

```
2026-10-02_oil_gas_africa_usa_lng_exports/
├── .github/workflows/ci.yml        # lint + smoke tests
├── config/config.yaml              # API, countries, indicators, paths, parameters
├── dags/pipeline_dag.py            # Airflow DAG (lng_exports_pipeline)
├── data/
│   ├── raw/                        # Comtrade + World Bank snapshots
│   └── processed/                  # generated CSVs (git-ignored)
├── docker/{Dockerfile,docker-compose.yml}
├── notebooks/analysis.ipynb        # exploratory analysis & visualisations
├── sql/queries.sql                 # 10 analytical SQL queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
└── requirements.txt
```

## How to Run

### 1. Local setup

```bash
cd 2026-10-02_oil_gas_africa_usa_lng_exports
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run     # validate config, no network
python src/ingestion/ingest.py               # refresh data/raw/ from the APIs
python src/transformation/transform.py       # build data/processed/*.csv
python src/loading/load.py --report          # load SQLite + write data/pipeline_report.txt
```

If an API is unreachable, ingestion keeps the existing raw snapshot (`pipeline.fallback_to_existing_raw: true`), so the downstream stages always run.

### 3. Explore the results

```bash
sqlite3 -header -column data/lng_pipeline.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker

```bash
cd docker
docker compose up --build        # runs ingest → transform → load → report
```

`../data` is mounted into the container, so the database and processed files persist on the host.

### 5. Airflow

```bash
export AIRFLOW_HOME=~/airflow
export LNG_PROJECT_ROOT=$(pwd)
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
airflow dags test lng_exports_pipeline 2026-10-02
```

### Configuration

All parameters live in `config/config.yaml`. Environment overrides: `CONFIG_PATH`, `DB_PATH`, `LOG_LEVEL`.

## Data Model

| Table | Grain | Key columns |
|-------|-------|-------------|
| `lng_trade_raw` | reporter × partner × flow × year | `trade_value_usd`, `qty_kg`, `partner` (resolved names; partner 0 = World, 842 = USA) |
| `wb_indicators` | country × indicator × year (long) | `indicator`, `indicator_name`, `value` |
| `wb_indicators_wide` | country × year | one column per indicator |
| `lng_exports_processed` | exporter × year | `export_value_billion_usd`, `usa_export_value_usd`, `usa_share_pct`, `export_share_of_africa_pct`, `num_destinations`, `top_destination`, `yoy_growth_pct`, WB indicators, `lng_share_of_merch_exports_pct`, `gas_dependency_score` |

**`gas_dependency_score`** (0–100) = 40% gas rents % GDP + 30% LNG share of merchandise exports + 30% electricity from gas, each min-max scaled across all exporter-years (weights configurable).

## Key Insights

1. **Nigeria dominates African LNG.** ≈USD 14.9 bn of LNG exports across 2019, 2020 and 2023 — 51% of the reporting countries' total in 2019, 75% in 2020 and 44% in 2023.
2. **Shock and rebound.** Combined exports fell from ≈USD 9.7 bn (2019) to ≈USD 5.3 bn (2020) and rebounded to ≈USD 13.5 bn in 2023 as Europe replaced Russian pipeline gas.
3. **New and growing suppliers.** Mozambique went from ≈USD 0.23 bn (2019) to ≈USD 1.66 bn (2023) after Coral Sul FLNG came online; Egypt (≈USD 2.57 bn) and Angola (≈USD 2.59 bn) roughly doubled versus 2019.
4. **USA flows are small but rising.** African LNG exports to the USA grew from ≈USD 21 m (2019) to ≈USD 263 m (2023), led by Nigeria (≈USD 219 m, 3.7% of its exports) and Angola (≈USD 44 m). Europe (Spain, France) and Asia (India, China) remain the main destinations; the USA is the top destination only for Gabon's negligible volumes.
5. **Gas rents do not equal prosperity.** Algeria (8.0%), Equatorial Guinea (6.6%), Libya (4.6%) and Mozambique (3.6%) have the highest gas rents as % of GDP (2021), yet the correlation with GDP per capita is weak (r ≈ 0.29); Mozambique pairs high gas rents with the lowest income in the group (≈USD 510).
6. **Domestic demand competes with exports.** Algeria (99%), Nigeria (77%) and Egypt (76%) generate most of their electricity from gas (2023), tying export capacity to domestic power needs.

## Technologies Used

- **Python 3.11** — pandas, numpy, requests, tenacity, PyYAML
- **SQLite** — analytical warehouse with PK-based upserts and window-function SQL
- **Jupyter, matplotlib, seaborn, plotly** — exploration and visualisation
- **Apache Airflow 2.8** — weekly orchestration
- **Docker / docker-compose** — reproducible execution
- **GitHub Actions** — flake8 linting, import and end-to-end smoke tests
