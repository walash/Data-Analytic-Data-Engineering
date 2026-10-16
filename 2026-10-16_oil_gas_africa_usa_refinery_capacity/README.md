# Oil & Gas Refinery Capacity & Fuel Access in Africa and USA

**Domain:** Oil & Gas · Energy Access · Trade  **Geography:** 14 African countries + United States  **Date:** 2026-10-16

An end-to-end data engineering project that ingests World Bank Open Data, builds a clean country-year panel, loads it into a SQLite warehouse and analyses Africa's **petroleum refining gap**: why some of the continent's largest crude producers still import a big share of their refined fuel, how that affects fuel and electricity access, and how the picture compares with the United States.

---

## Problem statement

Africa holds about 7% of the world's proven crude reserves, but for decades its major producers have exported crude and imported petrol, diesel and jet fuel back, often from European, Indian and US Gulf Coast refineries. The result is a **"crude-out, products-in" paradox**:

- import bills that swing with refined-product prices,
- costly fuel subsidies and recurring shortages at the pump,
- energy poverty: hundreds of millions of people without grid power who rely on generator fuel.

This project answers:

1. Which African countries show a structural **refining gap** (fuel-dominated exports *and* a heavy fuel-import bill)?
2. How has the **fuel-import burden** changed since 2016, including the 2022 price shock?
3. How dependent and how **volatile** are oil rents?
4. Does oil wealth translate into **electricity access**?
5. How does Africa compare with the **USA** on fuel trade, energy access and income?

> **Refining-gap proxy.** The World Bank does not publish refinery throughput, so a country-year is flagged `refining_gap_flag = 1` when it is an oil producer (oil rents ≥ 10% of GDP **or** fuel ≥ 50% of exports) **and** fuel is ≥ 15% of merchandise imports. Thresholds are set in `config/config.yaml`.

---

## Data sources

All data comes from the public **World Bank Open Data API** (no key needed), for NG, GH, AO, DZ, LY, EG, GA, CM, CD, ZA, SD, SS, TN, MA and US (`mrv=10`).

| Indicator | Code | Raw file | API endpoint |
|---|---|---|---|
| Oil rents (% of GDP) | `NY.GDP.PETR.RT.ZS` | `oil_rents_gdp.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/NY.GDP.PETR.RT.ZS?format=json&per_page=200&mrv=10) |
| Fuel exports (% of merchandise exports) | `TX.VAL.FUEL.ZS.UN` | `fuel_exports_pct.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/TX.VAL.FUEL.ZS.UN?format=json&per_page=200&mrv=10) |
| Fuel imports (% of merchandise imports) | `TM.VAL.FUEL.ZS.UN` | `fuel_imports_pct.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/TM.VAL.FUEL.ZS.UN?format=json&per_page=200&mrv=10) |
| Access to electricity (% of population) | `EG.ELC.ACCS.ZS` | `electricity_access.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/EG.ELC.ACCS.ZS?format=json&per_page=200&mrv=10) |
| Fossil fuel energy consumption (% of total) | `EG.USE.COMM.FO.ZS` | `fossil_fuel_consumption.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/EG.USE.COMM.FO.ZS?format=json&per_page=200&mrv=10) |
| GDP per capita (current US$) | `NY.GDP.PCAP.CD` | `gdp_per_capita.csv` | [link](https://api.worldbank.org/v2/country/NG;GH;AO;DZ;LY;EG;GA;CM;CD;ZA;SD;SS;TN;MA;US/indicator/NY.GDP.PCAP.CD?format=json&per_page=200&mrv=10) |

**Coverage notes:** oil rents run to 2021; fuel trade and GDP to 2025; electricity access to 2024. `EG.USE.COMM.FO.ZS` has real values only for 2015. The API returns `0.0` for later years, and `transform.py` treats those as missing. South Sudan has no trade or GDP data in this release.

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                    World Bank Open Data API (v2, JSON)                   │
 │  NY.GDP.PETR.RT.ZS │ TX.VAL.FUEL.ZS.UN │ TM.VAL.FUEL.ZS.UN │ EG.ELC.ACCS │
 │                    EG.USE.COMM.FO.ZS │ NY.GDP.PCAP.CD                    │
 └──────────────────────────────────┬───────────────────────────────────────┘
                                    │ HTTPS · pagination · tenacity retries
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1. INGEST   src/ingestion/ingest.py                                      │
 │    config-driven · exponential backoff · atomic writes · --dry-run       │
 │    ──► data/raw/*.csv  (country, iso3, year, value)  × 6                 │
 └──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM   src/transformation/transform.py                           │
 │    type-cast · dedupe · placeholder-zero → NULL · clip ranges            │
 │    balanced 15 × 2012-2025 grid · interior interpolation (+ flags)       │
 │    region / OPEC enrichment · refining-gap + archetype features          │
 │    ──► data/processed/master_dataset.csv                                 │
 │        data/processed/country_latest_snapshot.csv                        │
 │        data/processed/data_quality_report.csv                            │
 └──────────────────────────────────┬───────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3. LOAD   src/loading/load.py                ──► data/oil_gas_refinery.db│
 │    dim_country · dim_indicator · fact_energy_indicators                  │
 │    country_latest_snapshot · data_quality_report · pipeline_runs         │
 │    view: v_regional_yearly      (atomic transaction, FK checks)          │
 └───────────────┬───────────────────────────────┬──────────────────────────┘
                 ▼                               ▼
 ┌───────────────────────────────┐   ┌──────────────────────────────────────┐
 │ 4a. SQL  sql/queries.sql      │   │ 4b. EDA  notebooks/analysis.ipynb    │
 │  8 queries: CTEs, RANK, LAG,  │   │  6 visualisations (matplotlib,       │
 │  NTILE, rolling windows       │   │  seaborn, plotly) + narrative        │
 └───────────────────────────────┘   └──────────────────────────────────────┘

 Orchestration: Airflow DAG (dags/pipeline_dag.py)
     ingest_worldbank_data ─► transform_to_master ─► load_to_sqlite ─► generate_report
 Packaging: Docker + docker-compose (pipeline + sqlite-web)  ·  CI: GitHub Actions
```

---

## Data model

| Table | Grain | Key columns |
|---|---|---|
| `dim_country` | country | `iso3` PK, `iso2`, `region`, `subregion`, `opec_member` |
| `dim_indicator` | indicator | `wb_code`, `value_column`, `description` |
| `fact_energy_indicators` | country × year | 6 indicators, 6 `*_imputed` flags, `net_fuel_trade_pp`, `fuel_import_export_ratio`, `electricity_access_gap_pct`, `refining_gap_flag`, `energy_poverty_flag`, `fuel_trade_archetype`, `gdp_pc_pct_of_usa`, YoY deltas |
| `country_latest_snapshot` | country | latest non-null value **and year** per indicator |
| `data_quality_report` | indicator | non-null count, null %, imputations, year range |
| `pipeline_runs` | load × table | audit trail of row counts |

`fuel_trade_archetype` values: `Petro-exporter with refining gap`, `Petro-exporter`, `Net fuel importer`, `Diversified`, `Insufficient data`.

---

## How to run

### 1. Set up the environment
```bash
cd 2026-10-16_oil_gas_africa_usa_refinery_capacity
python -m venv .venv && source .venv/bin/activate
pip install pandas==2.2.3 numpy==1.26.4 requests==2.32.3 tenacity==9.0.0 PyYAML==6.0.2 \
            matplotlib==3.9.2 seaborn==0.13.2 plotly==5.24.1 jupyter==1.1.1
# Full stack incl. Airflow (use the official constraints file):
# pip install -r requirements.txt --constraint \
#   https://raw.githubusercontent.com/apache/airflow/constraints-2.9.3/constraints-3.11.txt
```

### 2. Run the pipeline
```bash
python src/ingestion/ingest.py --dry-run      # validate config, print URLs
python src/ingestion/ingest.py                # refresh data/raw from the API
python src/transformation/transform.py        # → data/processed/master_dataset.csv
python src/loading/load.py                    # → data/oil_gas_refinery.db
```
The raw CSVs are already in `data/raw/`, so you can skip ingestion and start at the transform step.

### 3. Query & explore
```bash
sqlite3 -header -column data/oil_gas_refinery.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker
```bash
cd docker
docker compose up --build pipeline      # runs ingest → transform → load
docker compose up -d sqlite-web         # browse the DB at http://localhost:8080
```

### 5. Airflow
```bash
export AIRFLOW_HOME=~/airflow
export OIL_GAS_PROJECT_ROOT=$(pwd)
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
airflow dags test oil_gas_africa_usa_refinery_capacity 2026-10-16
```
The DAG runs `@monthly` with 3 retries (exponential back-off) and writes `reports/pipeline_report_<ds>.md`.

### 6. CI
`.github/workflows/ci.yml` runs **flake8** on `src/` and `dags/`, then a smoke test: an ingestion dry run, transform + load on the committed raw data, row-count assertions, every SQL query, and notebook JSON validation.

---

## Key insights

All figures come from the current pipeline output (latest year shown in brackets).

1. **Nigeria is the clearest refining-gap case.** Fuel is ~86% of its exports but still ~33% of its imports (2025), and it is flagged in all 10 years with trade data. **Angola** (6 of 10 years) and **Cameroon** (5 of 8) show the same pattern. Nigeria's Dangote refinery, ramping up since 2024, is aimed squarely at this gap.
2. **Algeria, Libya and Gabon are self-sufficient exporters.** Fuel makes up 54–94% of their exports, but fuel imports stay low (1–13%) because domestic refining covers most local demand.
3. **The fuel-import burden is rising.** The average African fuel-import share went from ~10.6% (2016) to ~22.6% (2023). Ghana jumped from ~2–3% to 25–32% after 2021. The USA stayed within 5–10%.
4. **Oil rents are highly volatile.** Libya went 57.6% → 9.2% → 56.4% of GDP (2012 → 2020 → 2021). Angola, Gabon and Algeria lost about half their rents in the 2014–16 crash. US oil rents stay below 1% of GDP.
5. **Oil wealth does not buy energy access.** Across African countries the correlation between average oil rents and electricity access is about −0.11. Angola (≈25% rents) has 55.5% access and South Sudan 5.4%, while low-rent Morocco and Tunisia have 100%.
6. **The Africa–USA gap.** The average electricity-access gap narrowed from ~32 pp to ~26 pp (2016–2024). The income gap widened: US GDP per capita went from ~17× to ~23× the African average.

**Limitations:** trade shares are value-based and price-sensitive. The refining-gap flag is a trade proxy, not refinery nameplate capacity. Oil rents end in 2021 and fossil-fuel consumption in 2015.

---

## Technologies

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Ingestion | `requests`, `tenacity` (retry/backoff), `PyYAML` |
| Transformation | `pandas`, `numpy` |
| Storage | SQLite (star-style schema, view, indexes, FK checks) |
| Analysis | SQL (CTEs, window functions), Jupyter, `matplotlib`, `seaborn`, `plotly` |
| Orchestration | Apache Airflow 2.9 |
| Packaging | Docker, docker compose, `sqlite-web` |
| CI/CD | GitHub Actions (`flake8`, pipeline smoke test) |

---

## Project structure

```
2026-10-16_oil_gas_africa_usa_refinery_capacity/
├── README.md
├── requirements.txt
├── config/config.yaml              # endpoints, indicators, paths, thresholds, regions
├── data/
│   ├── raw/                        # 6 World Bank indicator CSVs
│   ├── processed/                  # master_dataset.csv, snapshot, quality report
│   └── oil_gas_refinery.db         # SQLite warehouse (created by load.py)
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/queries.sql                 # 8 analytical queries
├── notebooks/analysis.ipynb        # EDA with 6 visualisations
├── dags/pipeline_dag.py            # Airflow DAG
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/workflows/ci.yml
```

---

*Data: World Bank Open Data (CC BY 4.0). Refinery context (Dangote, Port Harcourt, Skikda, Ras Lanuf, Sogara) comes from public industry reporting and is used for interpretation only. It is not part of the dataset.*
