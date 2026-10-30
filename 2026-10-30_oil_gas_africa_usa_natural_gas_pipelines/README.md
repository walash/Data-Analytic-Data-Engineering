# Natural Gas Pipelines — Africa & USA Trade Flows and Infrastructure

**Domain:** Oil & Gas · **Geography:** Africa & USA · **Date:** 2026-10-30

An end-to-end data engineering project that measures how dependent ten African
gas economies and the United States are on natural gas. It looks at three angles:
gas-fired power at home, value from gas extraction, and hydrocarbon concentration
in exports. It sets these against a catalogue of the major cross-border pipelines
that carry African gas to Europe, the Middle East and neighbouring markets.

**Focus countries:** Algeria, Nigeria, Egypt, Libya, Tanzania, Mozambique, Ghana,
Cameroon, Gabon, Angola and the United States. Ten more African countries
(Benin, Côte d'Ivoire, Equatorial Guinea, Ethiopia, Kenya, Morocco, Tunisia,
South Africa, Senegal, Republic of Congo) are kept for regional context.

---

## Problem statement

Pipeline gas links North and West African producers to Europe and their
neighbours, and US shale gas has reshaped global supply. Policymakers and
investors need to know:

1. Which countries rely most on gas for electricity, and how is that changing?
2. How much do gas rents contribute to GDP, and which economies are most exposed?
3. How concentrated in fuels are merchandise exports, and how does the USA compare?
4. Where is pipeline export capacity located, and what is its operating status?
5. Which countries score highest on a composite energy-dependency measure?

## Data sources

| Dataset | Indicator | Source URL |
|---|---|---|
| Electricity production from natural gas (% of total) | `EG.ELC.NGAS.ZS` | https://api.worldbank.org/v2/country/all/indicator/EG.ELC.NGAS.ZS?format=json&per_page=1000&mrv=5 |
| Natural gas rents (% of GDP) | `NY.GDP.NGAS.RT.ZS` | https://api.worldbank.org/v2/country/all/indicator/NY.GDP.NGAS.RT.ZS?format=json&per_page=1000&mrv=5 |
| Fuel exports (% of merchandise exports) | `TX.VAL.FUEL.ZS.UN` | https://api.worldbank.org/v2/country/all/indicator/TX.VAL.FUEL.ZS.UN?format=json&per_page=1000&mrv=5 |
| Major gas pipeline catalogue | reference data | `config/config.yaml` → `reference_pipelines` (approximate nameplate capacities from public operator / press sources) |

The World Bank API is public and needs no key. `config/config.yaml` also has
optional slots (`EIA_API_KEY`, `IEA_API_KEY`, `COMTRADE_API_KEY`) for extending
the pipeline with flow-level trade data.

Raw extracts are committed in `data/raw/`:

| File | Rows | Countries | Years |
|---|---|---|---|
| `wb_ngas_electricity_pct.csv` | 76 | 21 | 2021–2025 |
| `wb_ngas_rents_gdp_pct.csv` | 105 | 21 | 2017–2021 |
| `wb_fuel_exports_pct.csv` | 80 | 19 | 2021–2025 |

## Architecture

```
 ┌───────────────────────────────────────────────────────────────────────────┐
 │                         SOURCES (public, no auth)                         │
 │  World Bank API: EG.ELC.NGAS.ZS │ NY.GDP.NGAS.RT.ZS │ TX.VAL.FUEL.ZS.UN    │
 │  config.yaml: reference_pipelines (Trans-Med, Medgaz, Greenstream, WAGP…) │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    │  HTTPS JSON, paginated
                                    ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                                       │
 │    requests + tenacity (exponential backoff on 429/5xx/timeouts)          │
 │    filter → Africa + USA ISO3 list, atomic CSV write                      │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    ▼   data/raw/*.csv
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM   src/transformation/transform.py  (pandas)                  │
 │    trim/upper-case codes · cast year→int, value→float · drop nulls        │
 │    range checks (0–100 %, 2000–2026) · dedupe (iso3, year) · region tags  │
 │    → countries · gas_indicators_long · country_year_panel (+YoY) · pipes  │
 └──────────────────────────────────┬────────────────────────────────────────┘
                                    ▼   data/processed/*.csv
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py  →  SQLite  data/natural_gas.db             │
 │    DDL with PK/FK/CHECK · single transaction · indexes · row-count check  │
 └───────────────┬───────────────────────┬───────────────────────┬───────────┘
                 ▼                       ▼                       ▼
     ┌───────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
     │ sql/queries.sql   │   │ notebooks/           │   │ 4. generate_report   │
     │ 10 analytical     │   │ analysis.ipynb       │   │ reports/pipeline_    │
     │ queries (CTEs,    │   │ 7 charts: matplotlib │   │ report_<ds>.md       │
     │ window functions) │   │ seaborn, plotly      │   │                      │
     └───────────────────┘   └──────────────────────┘   └──────────────────────┘

 Orchestration: Airflow DAG  ingest_data >> transform_data >> load_data >> generate_report
 Runtime:       Docker (python:3.11-slim) · docker compose (pipeline + Airflow profile)
 CI:            GitHub Actions (flake8, YAML/notebook validation, ETL + SQL + notebook run)
```

### Warehouse schema (SQLite)

| Table | Grain | Key columns |
|---|---|---|
| `countries` | one row per country | `iso3` (PK), `country_name`, `region`, `is_focus` |
| `gas_indicators` | country × year × indicator | `iso3`, `year`, `indicator_key`, `value` |
| `country_year_panel` | country × year (wide) | three indicators + year-over-year change columns |
| `pipeline_infrastructure` | one row per pipeline | `origin_iso3`, `destination`, `capacity_bcm_y`, `status` |

## How to run

### 1. Local environment

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run   # validate config, no network
python src/ingestion/ingest.py             # refresh data/raw/ from the World Bank API
python src/transformation/transform.py     # data/raw -> data/processed
python src/loading/load.py                 # data/processed -> data/natural_gas.db
```

If a download fails after all retries, ingestion logs the error and keeps the
existing raw file, so the later stages still run on the last good extract.

### 3. Explore the results

```bash
sqlite3 -header -column data/natural_gas.db < sql/queries.sql
jupyter nbconvert --to notebook --execute notebooks/analysis.ipynb --inplace
```

The notebook reads `data/raw/` directly, so it runs even before the ETL has been executed.

### 4. Docker

```bash
cd docker
docker compose up --build pipeline           # full ETL, data/ mounted as a volume
docker compose --profile airflow up -d       # Airflow standalone on http://localhost:8080
```

### 5. Airflow

Copy or symlink `dags/pipeline_dag.py` into your Airflow `dags/` folder and set
`NGAS_PROJECT_ROOT` to the project path. The DAG `africa_usa_natural_gas_pipelines`
runs monthly with 2 retries per task. Each run writes `reports/pipeline_report_<ds>.md`.

## Key insights (from the committed data)

| Country | Gas share of power (latest) | Gas rents % GDP (2021) | Fuel exports % (latest) |
|---|---|---|---|
| Algeria | 99.0 (2024) | 8.00 | 93.1 (2024) |
| Egypt | 76.9 (2024) | 2.05 | 9.8 (2025) |
| Nigeria | 72.9 (2024) | 1.16 | 85.9 (2025) |
| Libya | 69.5 (2024) | 4.58 | n/a |
| Ghana | 59.4 (2024) | 0.37 | 8.8 (2025) |
| Tanzania | 49.1 (2024) | 0.22 | 1.3 (2025) |
| **United States** | **39.6 (2025)** | **0.36** | **17.3 (2025)** |
| Gabon | 38.8 (2024) | 0.27 | 54.0 (2023) |
| Mozambique | 15.3 (2024) | 3.57 | 54.2 (2025) |
| Cameroon | 10.7 (2024) | 0.58 | 54.1 (2023) |
| Angola | 4.7 (2024) | 1.01 | 91.5 (2025) |

1. **Algeria is the most gas-dependent economy in the set.** About 99% of its
   power comes from gas, gas rents were 8.0% of GDP in 2021, and fuels are 93% of
   its exports. Its composite dependency score is 90.7/100 (Q6). Algeria is also
   the origin of 3 of the catalogued pipelines (~56 bcm/y of nameplate capacity).
2. **North African gas power is high but slipping.** Gas's share of electricity
   fell between 2021 and 2024 in Libya (−8.1 pp), Egypt (−4.8 pp), Nigeria
   (−4.4 pp) and Tanzania (−9.5 pp). Over the same window the USA rose
   2.3 pp to 39.6% in 2025.
3. **Export concentration and gas power are different risks.** Angola gets only
   4.7% of its power from gas but 91.5% of its exports are fuels. Egypt is the
   reverse: 76.9% gas power and 9.8% fuel exports.
4. **Gas rents rose sharply in 2021.** All eleven focus countries hit their
   2017–2021 peak gas rents in 2021, led by Algeria (8.0%), Libya (4.6%) and
   Mozambique (3.6%).
5. **The USA is a diversified exporter.** Fuels were 17.3% of US merchandise
   exports in 2025 and gas rents were 0.36% of GDP. The average across the African
   focus countries is about 50% fuel exports.
6. **Pipeline capacity is concentrated in North Africa.** The North Africa–Europe
   corridor holds about 56% of the ~115 bcm/y catalogued African capacity. The
   Maghreb–Europe line (12 bcm/y) has carried no Algerian gas since 2021. Nigeria's
   AKK line (~22.7 bcm/y) is still under construction.

**Limitations:** gas-rent estimates in this World Bank release end in 2021, and
Libya has no fuel-export observations. Pipeline capacities are approximate
nameplate values, not actual flows.

## Technologies

Python 3.11 · pandas · NumPy · requests · tenacity · PyYAML · SQLite ·
matplotlib · seaborn · Plotly · Jupyter · Apache Airflow 2.10 · Docker /
docker compose · GitHub Actions · flake8

## Project structure

```
2026-10-30_oil_gas_africa_usa_natural_gas_pipelines/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml              # sources, countries, pipeline catalogue, API key slots
├── data/
│   ├── raw/                     # World Bank extracts (committed)
│   ├── processed/               # generated by transform.py
│   └── natural_gas.db           # generated by load.py
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/
│   └── queries.sql              # 10 analytical queries
├── notebooks/
│   └── analysis.ipynb           # 7 visualisations
├── dags/
│   └── pipeline_dag.py          # Airflow DAG
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/workflows/
    └── ci.yml
```
