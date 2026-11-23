# Africa-USA Skilled Worker Visa Flows: A Data Engineering Pipeline

| | |
|---|---|
| **Domain** | Global Immigration |
| **Geographic focus** | Africa (10 countries: Egypt, Nigeria, Kenya, Ghana, Ethiopia, Morocco, Tanzania, Uganda, Senegal, South Africa) and the USA |
| **Period** | 2011-2025 (150 country-years) |
| **Stack** | Python, Apache Airflow, SQLite, Docker, Jupyter |

An end-to-end ETL pipeline that collects migration, remittance, income and education indicators for ten major
African source countries and the United States, engineers brain-drain metrics, loads them into a SQLite
analytics database and explores the results in a Jupyter notebook.

---

## Problem statement

Africa has the world's fastest-growing working-age population, and a rising share of its university graduates -
physicians, nurses, engineers and software developers - build careers abroad. The United States is a key
destination, admitting African professionals through employment-based channels such as the **H-1B
specialty-occupation visa** and employment-based green cards. For source countries this creates a
trade-off:

* **Brain drain** - loss of scarce skills in health, education and technology;
* **Brain gain / remittances** - diaspora earnings sent home now rival or exceed foreign direct investment.

Policymakers, universities and employers need a reproducible view of **which countries are losing people,
how much money flows back, whether emigration follows income or education, and how African sending pressure
compares with US absorption capacity**. This project answers those questions with official World Bank data
and a production-style pipeline; the derived `skilled_worker_proxy_score` estimates the relative size of each
country's educated, mobile population that competes for skilled-worker visas such as the H-1B.

---

## Data sources

| Source | URL | Used for |
|---|---|---|
| World Bank Open Data API | https://api.worldbank.org/v2/ | All indicators below (fetched by `src/ingestion/ingest.py`) |
| `SM.POP.NETM` | https://api.worldbank.org/v2/indicator/SM.POP.NETM | Net migration (10 African countries + USA) |
| `BX.TRF.PWKR.DT.GD.ZS` | https://api.worldbank.org/v2/indicator/BX.TRF.PWKR.DT.GD.ZS | Personal remittances received, % of GDP |
| `BX.TRF.PWKR.CD.DT` | https://api.worldbank.org/v2/indicator/BX.TRF.PWKR.CD.DT | Personal remittances received, current US$ |
| `NY.GDP.PCAP.CD` | https://api.worldbank.org/v2/indicator/NY.GDP.PCAP.CD | GDP per capita, current US$ |
| `SE.TER.ENRR` | https://api.worldbank.org/v2/indicator/SE.TER.ENRR | School enrollment, tertiary (% gross) |
| `SP.POP.TOTL` | https://api.worldbank.org/v2/indicator/SP.POP.TOTL | Population, total |
| USCIS H-1B Employer Data Hub | https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub | Reference for H-1B petition context and validation |
| DHS Yearbook of Immigration Statistics | https://www.dhs.gov/immigration-statistics/yearbook | Reference for lawful permanent residents / nonimmigrant admissions by country |

Raw files in `data/raw/`: `africa_migration_indicators.csv` (combined, 150 rows) and the untouched API payloads
`wb_net_migration.json`, `wb_remittances_pct_gdp.json`, `wb_remittances_usd.json`, `wb_gdp_per_capita.json`,
`wb_tertiary_enrollment.json`, `wb_population.json`, `wb_usa_net_migration.json`.
(`wb_skilled_emigration_rate.json` holds the API's "indicator not found / archived" response for the retired
skilled-emigration series and is not used by the pipeline.)

---

## Architecture

```
 ┌────────────────────────────┐
 │        RAW DATA            │   World Bank API v2 (6 indicators x 10 African countries
 │  api.worldbank.org/v2      │   + USA net migration), 2011-2025
 └─────────────┬──────────────┘
               │  HTTPS / JSON (tenacity: 3 attempts, exponential backoff)
               ▼
 ┌────────────────────────────┐   src/ingestion/ingest.py
 │        INGESTION           │   - one wb_*.json payload per indicator
 │                            │   - country x year grid -> africa_migration_indicators.csv
 └─────────────┬──────────────┘
               │  data/raw/
               ▼
 ┌────────────────────────────┐   src/transformation/transform.py
 │      TRANSFORMATION        │   - type coercion, de-duplication, per-country interpolation
 │                            │   - imputation flags, winsorised min-max normalisation
 │                            │   - remittances_per_capita, net_migration_rate_per_1000,
 │                            │     migration_intensity_index, skilled_worker_proxy_score
 └─────────────┬──────────────┘
               │  data/processed/*.csv
               ▼
 ┌────────────────────────────┐   src/loading/load.py
 │         LOADING            │   SQLite: data/migration_analytics.db
 │                            │   countries ─┐
 │                            │   migration_indicators (PK iso3+year, FK countries)
 │                            │   yearly_summary (incl. USA benchmark)
 └─────────────┬──────────────┘
               │
               ▼
 ┌────────────────────────────┐   sql/queries.sql          (8 analytical queries)
 │         ANALYSIS           │   notebooks/analysis.ipynb (EDA, 9 charts)
 │                            │   data/processed/pipeline_report.md
 └────────────────────────────┘

 Orchestration: dags/pipeline_dag.py (Airflow, @weekly)
   ingest_data ──► transform_data ──► load_data ──► generate_report
 Packaging: docker/Dockerfile + docker/docker-compose.yml  |  CI: .github/workflows/ci.yml
```

### Derived metrics

| Column | Definition |
|---|---|
| `remittances_per_capita` | remittances (US$) / population |
| `net_migration_rate_per_1000` | net migration / population x 1,000 (negative = net emigration) |
| `emigration_rate_per_1000` | max(-net migration rate, 0) |
| `migration_intensity_index` | 0-100; 50% \|net migration rate\| + 50% remittances % GDP (normalised) |
| `skilled_worker_proxy_score` | 0-100; 40% tertiary enrollment + 30% remittances per capita + 30% emigration rate (normalised) |

Normalised (`*_norm`) columns are winsorised at the 5th/95th percentile and min-max scaled across the panel;
weights live in `config/config.yaml`.

---

## How to run

### 1. Set up the environment

```bash
cd 2026-11-23_immigration_africa_usa_skilled_worker_visas
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run          # validate config, print API URLs (no network)
python src/ingestion/ingest.py                    # fetch World Bank data -> data/raw/
python src/transformation/transform.py            # clean + derive -> data/processed/
python src/loading/load.py --report               # SQLite -> data/migration_analytics.db + report
```

Environment overrides: `DB_PATH` (database location), `LOG_LEVEL` (`DEBUG`/`INFO`/...), `CONFIG_PATH`.

### 3. Query and explore

```bash
sqlite3 -header -column data/migration_analytics.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker

```bash
docker build -f docker/Dockerfile -t africa-usa-migration .
docker run --rm -v "$(pwd)/data:/app/data" africa-usa-migration
# or
cd docker && docker compose up --build
```

### 5. Airflow

Airflow is intentionally excluded from `requirements.txt` because it must be installed with its own constraints file:

```bash
pip install "apache-airflow==2.7.3" \
  --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.7.3/constraints-3.11.txt"
export AIRFLOW_HOME=~/airflow
export MIGRATION_PROJECT_ROOT="$(pwd)"
airflow db migrate
cp dags/pipeline_dag.py "$AIRFLOW_HOME/dags/"
airflow dags test africa_usa_skilled_worker_migration_pipeline 2026-11-23
```

### 6. Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: installs dependencies (without Airflow) on
Python 3.11, lints `src/` with flake8, compiles all modules, runs the ingestion dry-run smoke test and a
transform + load smoke test against the committed raw data.

---

## Key insights

Figures come from the World Bank data loaded in this pipeline (2011-2025).

1. **Persistent net senders.** Morocco and Kenya were net emigration countries in all 15 years; Ghana in 13 and
   Nigeria in 10. Uganda (~-1.2 per 1,000 on average) and Morocco (~-1.7 per 1,000) have the deepest
   population-adjusted outflows.
2. **Remittances are a macro-economic pillar.** Combined remittances to the ten countries rose from
   **US$49.8bn in 2011 to US$82.8bn in 2024** (9 of 10 countries reporting). Egypt received a record
   **US$41.5bn in 2025 (11.4% of GDP)**; Senegal averages ~10% of GDP. Egypt and Nigeria together receive
   60-73% of the ten-country total every year.
3. **Fastest-growing diasporas.** Ethiopia (~22% CAGR) and Kenya (~13% CAGR) show the fastest remittance growth,
   consistent with a rapidly expanding professional diaspora.
4. **Brain-drain exposure.** Morocco combines high tertiary enrollment (~35%) with constant net emigration and tops
   the skilled-worker proxy ranking, followed by Egypt, Senegal, Ghana and Kenya.
5. **Migration hump, not poverty push.** The pooled GDP-migration correlation (r = 0.34) is driven by South Africa;
   without it the relationship turns slightly negative (r = -0.18). Middle-income countries send more than the
   poorest ones, consistent with skilled migration requiring resources and credentials.
6. **South Africa plays a dual role** - the region's largest net receiver (net migration positive in 14 of 15
   years) while still losing professionals, so net figures understate skilled outflows.
7. **US absorption has not recovered.** US net migration fell from ~1.89m (2016) to ~330k (2020) and stood near
   1.23m in 2025, while the number of the ten African countries that were net senders rose from 4-6 (2016-2021)
   to 7-8 (2022-2025) - implying intensifying competition for H-1B and employment-based visa slots.

**Limitations:** World Bank net migration is a modelled residual without destination breakdown; tertiary
enrollment is partially imputed (flagged); the skilled-worker score is a relative proxy, not a visa count.
Joining USCIS H-1B approvals and DHS admissions by country of birth is the natural next extension.

---

## Technologies

| Technology | Role |
|---|---|
| Python 3.11 (pandas, numpy, requests, tenacity, PyYAML) | Ingestion, transformation, loading |
| Apache Airflow 2.7 | Weekly orchestration (`dags/pipeline_dag.py`) |
| SQLite | Analytical store (`data/migration_analytics.db`) |
| Docker / Docker Compose | Reproducible containerised runs |
| Jupyter, matplotlib, seaborn, plotly | Exploratory analysis and visualisation |
| GitHub Actions, flake8 | CI linting and smoke tests |

---

## Project structure

```
2026-11-23_immigration_africa_usa_skilled_worker_visas/
├── README.md
├── requirements.txt
├── config/config.yaml                 # API, paths, countries, years, weights, logging
├── src/
│   ├── ingestion/ingest.py            # World Bank API -> data/raw/
│   ├── transformation/transform.py    # cleaning + derived metrics -> data/processed/
│   └── loading/load.py                # SQLite load + Markdown report
├── sql/queries.sql                    # 8 analytical queries
├── notebooks/analysis.ipynb           # EDA notebook
├── dags/pipeline_dag.py               # Airflow DAG
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .github/workflows/ci.yml           # GitHub Actions CI
└── data/
    ├── raw/                           # API payloads + combined CSV
    ├── processed/                     # processed CSVs + pipeline_report.md
    └── migration_analytics.db
```
