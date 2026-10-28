# Tuberculosis Burden in Africa & the USA — A Data Engineering Pipeline

**Domain:** Health / Infectious Disease Epidemiology
**Geographic focus:** 54 African Union member states (North, West, Central, East, Southern Africa) and the United States
**Period covered:** 2000–2024 (incidence) · 2015–2024 (mortality, treatment success) · 2020–2024 (health expenditure)

---

## Problem Statement

Tuberculosis is again the world's leading infectious killer, and Africa carries a disproportionate
share of the burden, while the United States has one of the lowest rates on Earth — yet US cases
have been rising since 2020. The relevant indicators are scattered across two public APIs (WHO and
World Bank) with different formats, country coding, coverage windows and null conventions.

This project builds a reproducible, scheduled ETL pipeline that consolidates these sources into an
analytical SQLite warehouse to answer:

1. How large is the TB incidence and mortality gap between Africa and the USA, and is it closing?
2. Which African countries and sub-regions carry the highest burden, and where is progress fastest?
3. How do deaths relate to cases (case-fatality proxy) across countries?
4. Which countries meet the WHO 90 % treatment-success target?
5. Is health spending (% of GDP) associated with lower TB burden?

## Data Sources (public, no API key required)

| Dataset | Provider | Indicator | Endpoint |
|---|---|---|---|
| TB incidence per 100k (with low/high bounds) | WHO Global Health Observatory | `MDG_0000000020` | https://ghoapi.azureedge.net/api/MDG_0000000020 |
| TB death rate per 100k | World Bank | `SH.TBS.MORT` | https://api.worldbank.org/v2/country/all/indicator/SH.TBS.MORT?format=json&per_page=500&mrv=10 |
| TB treatment success rate (% new cases) | World Bank | `SH.TBS.CURE.ZS` | https://api.worldbank.org/v2/country/all/indicator/SH.TBS.CURE.ZS?format=json&per_page=500&mrv=10 |
| Current health expenditure (% GDP) | World Bank | `SH.XPD.CHEX.GD.ZS` | https://api.worldbank.org/v2/country/all/indicator/SH.XPD.CHEX.GD.ZS?format=json&per_page=500&mrv=5 |

## Architecture

```
 ┌──────────────────────────┐   ┌──────────────────────────────────────┐
 │ WHO GHO OData API        │   │ World Bank v2 API (paginated JSON)   │
 │ MDG_0000000020           │   │ SH.TBS.MORT · SH.TBS.CURE.ZS ·       │
 │ (TB incidence + bounds)  │   │ SH.XPD.CHEX.GD.ZS                    │
 └────────────┬─────────────┘   └───────────────────┬──────────────────┘
              │  HTTPS + retry/backoff (tenacity)   │
              └──────────────────┬──────────────────┘
                                 ▼
             ┌────────────────────────────────────────┐
             │ 1. INGEST   src/ingestion/ingest.py    │
             │  raw JSON (all pages) + Africa/USA CSV │──►  data/raw/
             └────────────────────┬───────────────────┘
                                  ▼
             ┌────────────────────────────────────────┐
             │ 2. TRANSFORM  src/transformation/      │
             │  clean · cast · dedupe · geo-tag ·     │──►  data/processed/
             │  burden bands · YoY · CFR · merge      │
             └────────────────────┬───────────────────┘
                                  ▼
             ┌────────────────────────────────────────┐
             │ 3. LOAD     src/loading/load.py        │
             │  dim_country + 4 fact tables +         │──►  data/tb_burden.db
             │  tb_burden_summary + latest view       │     (SQLite)
             └────────────────────┬───────────────────┘
                                  ▼
       ┌──────────────────────────┼──────────────────────────┐
       ▼                          ▼                          ▼
 sql/queries.sql        notebooks/analysis.ipynb     generate_report (Airflow)
 (10 analytical SQL)    (8 figures, EDA narrative)   data/reports/run_report_*.txt

 Orchestration: Airflow DAG  ingest → transform → load → generate_report
                schedule "0 6 * * 1,3,5" (06:00 UTC Mon/Wed/Fri), 3 retries
 Packaging:     Docker image + docker-compose (named SQLite volume)
 CI:            GitHub Actions — flake8 lint + ingestion smoke test
```

### Warehouse schema

| Table | Grain | Key columns |
|---|---|---|
| `dim_country` | country | `country_code` (PK), `country_name`, `region` (Africa/USA), `sub_region` |
| `fact_tb_incidence` | country-year | incidence, low/high estimate, uncertainty range, burden category, YoY % |
| `fact_tb_mortality` | country-year | `tb_mortality_per_100k` |
| `fact_tb_treatment` | country-year | `tb_treatment_success_pct` (0–100 check) |
| `fact_health_expenditure` | country-year | `health_expenditure_pct_gdp` |
| `tb_burden_summary` | country-year | merged + enriched: case-fatality ratio, gap to 90 % target, completeness |
| `v_latest_country_burden` | country | view: most recent year per country |

## How to Run

### 1. Local setup

```bash
cd 2026-10-28_health_africa_usa_tuberculosis_burden
python -m venv .venv && source .venv/bin/activate
pip install pandas==2.3.3 numpy==1.26.4 requests==2.32.3 tenacity==9.0.0 PyYAML==6.0.2 \
            matplotlib==3.9.2 seaborn==0.13.2 plotly==5.24.1 jupyter==1.1.1
# or everything, including Airflow and flake8:
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py                 # download WHO + World Bank data → data/raw/
python src/transformation/transform.py         # clean/merge → data/processed/
python src/loading/load.py                     # build data/tb_burden.db
```

Useful ingestion flags:

```bash
python src/ingestion/ingest.py --dry-run                       # validate config & URLs, no writes
python src/ingestion/ingest.py --dry-run --check-connectivity  # probe each API once
python src/ingestion/ingest.py --from-cache                    # rebuild CSVs from saved raw JSON (offline)
```

### 3. Explore the results

```bash
sqlite3 -header -column data/tb_burden.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker

```bash
cd docker
docker compose up --build                       # runs ingest → transform → load
docker compose run --rm sqlite                  # sqlite3 shell on the named volume
```

### 5. Airflow

```bash
export AIRFLOW_HOME=~/airflow
pip install "apache-airflow==2.10.5" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt"
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/   # or symlink the project's dags/ folder
airflow standalone                            # DAG id: africa_usa_tuberculosis_burden_pipeline
```

Configuration (endpoints, paths, retry policy, burden bands, the 54 African country codes) lives in
`config/config.yaml`; `CONFIG_PATH`, `LOG_LEVEL` and `DB_PATH` environment variables override it.

## Key Insights (latest run: data through 2024)

- **A 50–80x gap.** The average African country recorded **180 TB cases per 100k** in 2024 versus
  **3.2 in the USA** — 56 times higher. The worst-affected countries — Lesotho (548), Djibouti
  (495), Namibia (422), Central African Republic (419) and South Africa (389) — are 120–170x the US rate.
- **Uneven progress.** Since 2010, mean incidence fell **61 % in Southern Africa** and **46 % in East
  Africa**, but only 31 % in West and 27 % in Central Africa. Botswana (-70 %), South Africa (-64 %)
  and Eswatini (-63 %) achieved the biggest ten-year reductions (2014→2024).
- **Fewer "very high" burden countries.** African countries at ≥300 per 100k fell from 21 (2000) to 13 (2024).
- **Mortality.** African TB deaths average 23.2 per 100k vs 0.16 in the USA; deaths per estimated
  case range from ~3 % to ~26 % across African countries (USA ≈5 %).
- **Treatment success inverts the picture.** 20 of 54 African countries meet the WHO 90 % target
  (Africa mean 84 %), while the USA reports 72 % (latest, 2022).
- **US cases are rising again.** US incidence dropped to 2.4/100k in 2020 (COVID-era
  under-diagnosis) and rebounded to 3.2/100k by 2024.
- **Spending share is not destiny.** Health expenditure as % of GDP is essentially uncorrelated with
  TB incidence across Africa (Spearman ρ ≈ -0.05); HIV co-infection and absolute resources matter more.

*Caveats: Africa aggregates are unweighted country means; WHO incidence figures are modelled
estimates with wide uncertainty intervals.*

## Technologies

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Ingestion | `requests`, `tenacity` (exponential-backoff retries), `PyYAML` |
| Transformation | `pandas`, `numpy` |
| Storage | SQLite (star-like schema, indexes, views, FK + CHECK constraints) |
| Analysis | SQL window functions, Jupyter, `matplotlib`, `seaborn`, `plotly` |
| Orchestration | Apache Airflow 2.10 |
| Packaging | Docker, Docker Compose |
| CI | GitHub Actions, `flake8` |

## Project Structure

```
2026-10-28_health_africa_usa_tuberculosis_burden/
├── .github/workflows/ci.yml        # lint + ingestion smoke test
├── config/config.yaml              # endpoints, paths, pipeline parameters, geography
├── dags/pipeline_dag.py            # Airflow DAG (Mon/Wed/Fri 06:00 UTC)
├── data/
│   ├── raw/                        # API payloads + Africa/USA CSV extracts
│   ├── processed/                  # cleaned & merged CSVs
│   └── tb_burden.db                # SQLite warehouse
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA with 8 figures
├── sql/queries.sql                 # 10 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```
