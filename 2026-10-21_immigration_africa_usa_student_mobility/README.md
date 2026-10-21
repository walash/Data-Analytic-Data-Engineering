# African Student Mobility to/from the USA — A Data Engineering Pipeline

**Domain:** Global Immigration · **Sub-theme:** African student mobility to/from the USA · **Date:** 2026-10-21

An end-to-end, reproducible ETL pipeline that pulls six World Bank development indicators for
**15 African countries and the USA**, cleans and enriches them into a small star-schema SQLite
warehouse, and analyses the **push factors behind African students going abroad to study**,
with the USA as the main destination and benchmark.

---

## Problem statement

The USA is one of the top destinations for internationally mobile African students. Direct,
bilateral student-flow data (UNESCO UIS, IIE Open Doors) is licensed, lagged or gated, but the
**drivers** of outbound study are openly published by the World Bank. This project asks:

1. How large is the tertiary-education **capacity gap** between African countries and the USA?
2. Which countries face the strongest combined **push factors** (low domestic tertiary capacity, low
   income, remittance dependence, net emigration)?
3. How concentrated are **diaspora remittances**, a key funding channel for study abroad?
4. Is African income **converging or diverging** from the USA (which shapes affordability of US study)?
5. How do **migration flows** of the USA (destination) compare with African origin countries?

## Geographic scope

| Sub-region | Countries (ISO3) |
|---|---|
| West Africa | Nigeria (NGA), Ghana (GHA), Senegal (SEN), Côte d'Ivoire (CIV) |
| East Africa | Kenya (KEN), Ethiopia (ETH), Tanzania (TZA), Uganda (UGA), Rwanda (RWA) |
| North Africa | Egypt (EGY), Morocco (MAR), Tunisia (TUN) |
| Southern Africa | South Africa (ZAF), Zambia (ZMB) |
| Central Africa | Cameroon (CMR) |
| Benchmark / destination | United States (USA) |

## Data sources

All data comes from the public **World Bank Indicators API v2** (World Development Indicators); no API
key is required. Endpoint pattern:
`https://api.worldbank.org/v2/country/{ISO3;ISO3;...}/indicator/{CODE}?format=json&date=2000:2025&per_page=1000`

| File (`data/raw/`) | Indicator | Code | Unit |
|---|---|---|---|
| `wb_net_migration.json` | Net migration | `SM.POP.NETM` | persons |
| `wb_tertiary_enrollment.json` | School enrollment, tertiary (% gross) | `SE.TER.ENRR` | % |
| `wb_education_expenditure.json` | Government expenditure on education | `SE.XPD.TOTL.GD.ZS` | % of GDP |
| `wb_remittances.json` | Personal remittances, received | `BX.TRF.PWKR.CD.DT` | current US$ |
| `wb_gdp_per_capita.json` | GDP per capita | `NY.GDP.PCAP.CD` | current US$ |
| `wb_population.json` | Population, total | `SP.POP.TOTL` | persons |

Every raw file keeps the native World Bank layout `[metadata_dict, list_of_records]`; each record has
`indicator, country, countryiso3code, date, value, unit, obs_status, decimal`. The current snapshot holds
416 records per indicator (16 countries × 26 years, 2000–2025; World Bank `lastupdated` 2026-10-08).

## Architecture

```
                    +----------------------------------------------+
                    |   World Bank Indicators API v2 (public)      |
                    |   SM.POP.NETM  SE.TER.ENRR  SE.XPD.TOTL.GD.ZS |
                    |   BX.TRF.PWKR.CD.DT  NY.GDP.PCAP.CD  SP.POP.TOTL |
                    +----------------------+-----------------------+
                                           | HTTPS JSON, paginated,
                                           | retries + exp. backoff
                                           v
 +-------------------+     +---------------------------------------+
 | config/config.yaml|---->|  1. INGEST  src/ingestion/ingest.py   |
 | indicators, ISO3, |     |  16 countries x 6 indicators          |
 | paths, weights    |     +-------------------+-------------------+
 +-------------------+                         |  data/raw/wb_*.json
          |                                    v
          |              +---------------------------------------------+
          +------------->|  2. TRANSFORM  src/transformation/transform.py |
          |              |  parse -> filter Africa-15 + USA -> cast ->  |
          |              |  dedupe -> sanity rules -> interpolate gaps  |
          |              |  -> pivot -> derive per-capita, %-of-USA,    |
          |              |  YoY and Student Mobility Pressure Index     |
          |              +-------------------+-------------------------+
          |                                  |  data/processed/*.csv
          |                                  v
          |              +---------------------------------------------+
          +------------->|  3. LOAD  src/loading/load.py               |
                         |  DDL + FKs + indexes + validation + audit   |
                         +-------------------+-------------------------+
                                             |  data/student_mobility.db
                     +-----------------------+-----------------------+
                     v                       v                       v
        +----------------------+ +----------------------+ +------------------------+
        | sql/queries.sql      | | notebooks/           | | 4. REPORT (Airflow)    |
        | 8 window/aggregate   | | analysis.ipynb       | | reports/               |
        | analytical queries   | | 7+ charts (mpl/sns/  | | pipeline_report.md     |
        +----------------------+ | plotly)              | +------------------------+
                                 +----------------------+

  Orchestration: dags/pipeline_dag.py   ingest_worldbank >> transform_indicators
                                        >> load_sqlite >> generate_report  (@monthly)
  Packaging:     docker/Dockerfile + docker/docker-compose.yml (pipeline + sqlite-web UI)
  CI:            .github/workflows/ci.yml (flake8 lint -> transform/load/SQL smoke test)
```

## Warehouse schema (`data/student_mobility.db`)

| Table | Grain | Description |
|---|---|---|
| `countries` | iso3 | Dimension: name, region, sub-region, `is_africa`, `is_usa` |
| `indicators` | indicator_key | Dimension: WDI code, name, unit, source |
| `indicator_observations` | iso3 × indicator × year | Long fact table, `is_imputed` flag |
| `country_year_metrics` | iso3 × year | Wide fact table + derived metrics (below) |
| `mobility_snapshot` | iso3 (Africa) | Latest non-null value per metric, with its year, and pressure rank |
| `pipeline_runs` | run × table | Load audit log (timestamp, row count) |

**Derived metrics** in `country_year_metrics`: `net_migration_rate_per_1000`,
`remittances_per_capita_usd`, `remittances_pct_gdp`, `education_spend_per_capita_usd`,
`gdp_pc_pct_of_usa`, `enrollment_gap_vs_usa_pp`, `edu_spend_pc_pct_of_usa`, YoY % changes,
`income_group_proxy`, `enrollment_reference_year` and `mobility_pressure_index`.

### Student Mobility Pressure Index (0–100)

Computed per year across the 15 African countries with min-max scaling:

| Component | Weight | Direction |
|---|---:|---|
| Tertiary capacity gap (low enrollment) | 0.35 | higher gap → more pressure |
| Income gap (low log GDP per capita) | 0.35 | lower income → more pressure |
| Remittance intensity (% of GDP) | 0.15 | higher → stronger diaspora channel |
| Net emigration rate (per 1,000) | 0.15 | more emigration → more pressure |

Weights live in `config/config.yaml`. Because tertiary enrollment is reported with long lags, each
country's last observed enrollment is carried forward and the year used is stored in
`enrollment_reference_year`. Missing secondary components are handled by re-weighting; the index is only
computed when both core components are present.

## How to run

```bash
cd 2026-10-21_immigration_africa_usa_student_mobility
python -m venv .venv && source .venv/bin/activate
grep -v '^apache-airflow' requirements.txt > requirements-runtime.txt
pip install -r requirements-runtime.txt

python src/ingestion/ingest.py            # fetch fresh data (add --dry-run to only print URLs)
python src/transformation/transform.py    # -> data/processed/*.csv
python src/loading/load.py                # -> data/student_mobility.db

sqlite3 data/student_mobility.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### Docker

```bash
docker compose -f docker/docker-compose.yml up --build
# pipeline container runs ingest -> transform -> load -> SQL queries (reports/query_results.txt)
# sqlite-web UI then serves the database read-only at http://localhost:8080
```

### Airflow

Install Airflow with its constraints file, then point it at this folder's `dags/`:

```bash
pip install "apache-airflow==2.10.2" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.2/constraints-3.11.txt"
export AIRFLOW__CORE__DAGS_FOLDER=$(pwd)/dags
airflow standalone
# trigger DAG: africa_usa_student_mobility_pipeline
```

The `generate_report` task writes `reports/pipeline_report.md` (row counts, pressure ranking, USA benchmarks).

### Configuration and API keys

`config/config.yaml` holds indicator codes, country list, paths and index weights. The World Bank API needs
**no key**; the `api_keys` section contains clearly labelled placeholders (`YOUR_..._HERE`) only for optional
extensions (UNESCO UIS, IIE Open Doors, SMTP alerts). Prefer environment variables for real secrets.

## Key insights (data snapshot of 2026-10-21)

Figures below come from running `sql/queries.sql` and the notebook against the current database.

1. **Huge capacity gap.** In 2022 (latest USA value) US gross tertiary enrollment was **79.4%**, against an
   Africa-15 median of **16.4%**. Uganda (4.6%) and Ethiopia (5.9%) were below 7% of the US rate in their
   latest common year (2014).
2. **East Africa faces the strongest push.** 2025 pressure index: **Ethiopia 88.6, Uganda 85.6,
   Tanzania 84.4, Rwanda 80.4, Nigeria 78.4**. Lowest: **South Africa 19.5**, Morocco 28.3, Tunisia 34.2.
3. **Income and domestic capacity move together.** Spearman ρ = **0.85** between GDP per capita and tertiary
   enrollment across African countries (latest paired year).
4. **Remittances are highly concentrated.** In 2024 Egypt (US$29.6bn) and Nigeria (US$22.1bn) received
   **~58%** of the 15-country total; Morocco adds another 14%. Ethiopia's inflows grew ~557% since 2015.
5. **Income divergence from the USA.** 11 of 15 countries lost ground vs US GDP per capita since 2015;
   Nigeria fell from 4.55% to 1.36% of the US level and South Africa from 10.75% to 7.33%, making US study
   costs relatively heavier. Ghana, Côte d'Ivoire, Senegal and Kenya edged up.
6. **Migration asymmetry.** The USA recorded 1.23–1.89m net migrants a year in 2010–2025, except the COVID dip
   (~0.33m in 2020, ~0.67m in 2021), while 10 of 15 African focus countries were net senders in 2025.
7. **Thin education data.** Tertiary enrollment is missing after 2011 for Nigeria, after 2014 for Ethiopia and
   Uganda, and after 2012 for Zambia — a reason to pair this pipeline with UNESCO UIS mobility data.

## Technologies

Python 3.11 · pandas · NumPy · requests + tenacity · PyYAML · SQLite (window functions) · matplotlib ·
seaborn · Plotly · Jupyter · Apache Airflow 2.10 · Docker / Docker Compose · sqlite-web · GitHub Actions ·
flake8

## Project structure

```
2026-10-21_immigration_africa_usa_student_mobility/
├── README.md
├── requirements.txt
├── config/config.yaml
├── data/
│   ├── raw/                     # World Bank JSON (ingest output)
│   ├── processed/               # cleaned CSVs (transform output)
│   └── student_mobility.db      # SQLite warehouse (load output)
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

## Limitations

* The World Bank publishes **drivers**, not bilateral Africa→USA student counts; the pressure index is a
  composite proxy, not a measured flow.
* GDP and remittances are in current US$, so exchange-rate shocks (e.g. Nigeria's naira devaluation) appear
  as large swings.
* Up to 3 consecutive missing years are linearly interpolated (flagged `is_imputed = 1`); longer gaps stay null.

## License

Code: MIT. Data: World Bank WDI, licensed CC BY 4.0.
