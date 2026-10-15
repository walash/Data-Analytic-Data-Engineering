# HIV/AIDS Burden in Africa & the USA — A Data Engineering Pipeline

**Domain:** Global Health · **Focus:** 15 African countries + United States · **Period:** 2014–2024

An end-to-end ETL pipeline that pulls five HIV/AIDS and health-financing indicators
from the World Bank Indicators API, cleans and merges them into an analysis-ready
country-year table, loads them into a SQLite star schema, and analyses the results
with SQL and a Jupyter notebook. The pipeline is containerised with Docker,
orchestrated with Apache Airflow and validated by GitHub Actions.

---

## Problem statement

Sub-Saharan Africa carries the majority of the world's HIV burden, while the United
States has the world's highest health spending. This project answers:

1. Which countries have the highest HIV prevalence and the largest number of people living with HIV (PLHIV)?
2. How fast has antiretroviral therapy (ART) coverage scaled up since 2015?
3. How large is the remaining **treatment gap** (PLHIV not on ART), and where is it concentrated?
4. Which countries reach the UNAIDS **90-90-90** (and 95-95-95) treatment levels?
5. How does health expenditure relate to HIV outcomes, and how does Africa compare with the USA?

## Countries

| Subregion | Countries |
|---|---|
| Southern Africa | South Africa (ZA), Mozambique (MZ), Zimbabwe (ZW), Zambia (ZM), Malawi (MW) |
| East Africa | Kenya (KE), Uganda (UG), Tanzania (TZ), Ethiopia (ET) |
| West Africa | Nigeria (NG), Ghana (GH), Cote d'Ivoire (CI) |
| Central Africa | Cameroon (CM) |
| North Africa | Egypt (EG), Morocco (MA) |
| North America | United States (US) |

## Data sources

All data is public and comes from the World Bank Indicators API (v2), JSON format, last 10 years available (`mrv=10`):

| Indicator | ID | Endpoint |
|---|---|---|
| Prevalence of HIV, total (% of population ages 15-49) | `SH.DYN.AIDS.ZS` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;ZM;MW;MZ;ZW;CM;CI;US/indicator/SH.DYN.AIDS.ZS?format=json&per_page=200&mrv=10 |
| Antiretroviral therapy coverage (% of people living with HIV) | `SH.HIV.ARTC.ZS` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;ZM;MW;MZ;ZW;CM;CI;US/indicator/SH.HIV.ARTC.ZS?format=json&per_page=200&mrv=10 |
| Incidence of HIV, ages 15-49 (per 1,000 uninfected population ages 15-49) | `SH.HIV.INCD.ZS` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;ZM;MW;MZ;ZW;CM;CI;US/indicator/SH.HIV.INCD.ZS?format=json&per_page=200&mrv=10 |
| Adults (ages 15+) and children (0-14 years) living with HIV | `SH.HIV.TOTL` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;ZM;MW;MZ;ZW;CM;CI;US/indicator/SH.HIV.TOTL?format=json&per_page=200&mrv=10 |
| Current health expenditure (% of GDP) | `SH.XPD.CHEX.GD.ZS` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;ZM;MW;MZ;ZW;CM;CI;US/indicator/SH.XPD.CHEX.GD.ZS?format=json&per_page=200&mrv=10 |

HIV indicators cover 2015–2024. Health expenditure covers 2014–2023.

> **Data limitation:** the World Bank (which republishes UNAIDS estimates) reports **no
> HIV prevalence, incidence, ART or PLHIV values for the United States**. These fields
> are null for all US rows in the source data (10 of 160 rows per HIV indicator). The
> pipeline keeps them as nulls and does not impute them. The Africa-vs-USA comparison
> therefore uses health expenditure. For context outside this dataset, the US CDC
> estimates about 1.2 million people were living with HIV in the US in 2022.

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                World Bank Indicators API  (api.worldbank.org/v2)         │
 │  SH.DYN.AIDS.ZS · SH.HIV.ARTC.ZS · SH.HIV.INCD.ZS · SH.HIV.TOTL ·        │
 │  SH.XPD.CHEX.GD.ZS     ×  16 countries  ×  10 most recent years          │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     │ HTTPS + tenacity retry/backoff
                                     ▼
 ┌──────────────────────────┐   src/ingestion/ingest.py   (--dry-run for CI)
 │  INGEST                  │──► data/raw/<indicator>.json  (raw API payload)
 │                          │──► data/raw/<indicator>.csv   (flattened)
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐   src/transformation/transform.py
 │  TRANSFORM               │   type casting · dedup · null handling · outer merge
 │                          │   derived: plhiv_on_art, treatment_gap, 90-90-90 flags, YoY
 │                          │──► data/processed/hiv_aids_processed.csv (country-year)
 │                          │──► data/processed/hiv_aids_summary.csv   (country)
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐   src/loading/load.py
 │  LOAD (SQLite)           │──► data/hiv_aids.db
 │   countries (dim)        │        countries ──┐
 │   indicators (dim)       │        indicators  │ 1:N
 │   hiv_metrics (fact)     │        hiv_metrics ◄┘  PK(country_code, year)
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┬──────────────────────────┬───────────────────┐
 │ sql/queries.sql (8 Qs)   │ notebooks/analysis.ipynb │ generate_report   │
 │                          │ (8 visualisations)       │ (Airflow task)    │
 └──────────────────────────┴──────────────────────────┴───────────────────┘

 Orchestration: Airflow DAG `health_africa_usa_hiv_aids_pipeline` (Mon/Wed/Fri 06:00 UTC)
   ingest_data ──► transform_data ──► load_to_db ──► generate_report
 Packaging: Docker / docker-compose      CI: GitHub Actions (flake8, py_compile, dry-run)
```

## Data model

| Table | Grain | Key columns |
|---|---|---|
| `countries` | 1 row per country (16) | `country_code` PK, `iso3`, `country_name`, `region` (Africa/USA), `subregion` |
| `indicators` | 1 row per indicator (5) | `indicator_id` PK, `indicator_key`, `value_column`, `indicator_name`, `unit` |
| `hiv_metrics` | 1 row per country-year (175) | `hiv_prevalence_pct`, `art_coverage_pct`, `hiv_incidence_per1000`, `plhiv_count`, `health_exp_pct_gdp`, `plhiv_on_art`, `treatment_gap`, `treatment_gap_pct`, `meets_90_90_90_art`, `meets_95_95_95_art`, `*_yoy_change` |

**Derived metrics**

- `plhiv_on_art = plhiv_count × art_coverage_pct / 100`
- `treatment_gap = plhiv_count − plhiv_on_art`: estimated PLHIV not receiving ART
- `meets_90_90_90_art`: ART coverage ≥ 81% (90% diagnosed × 90% treated). This checks the treatment level only. Viral-suppression data is not in the source.
- `meets_95_95_95_art`: ART coverage ≥ 90.25%

## Key insights (World Bank data, latest year 2024)

1. **Southern Africa has the highest prevalence.** Adult HIV prevalence is 17.2% in South Africa, 11.5% in Mozambique, 9.8% in Zimbabwe and 9.4% in Zambia. Egypt and Morocco are at 0.1%.
2. **The burden is concentrated in a few countries.** The 15 African countries have 22.5 million PLHIV. South Africa alone has 7.8 million (≈35%), and the top five (South Africa, Mozambique, Nigeria, Tanzania, Uganda) hold ≈69%.
3. **ART coverage has scaled up a lot.** Average ART coverage rose from 44.9% (2015) to 80.4% (2024). Cameroon gained the most (+58 pp, 30% → 88%). 11 of 15 countries are now at or above the 81% 90-90-90 treatment level. Zimbabwe (95%) and Zambia (94%) are above the 95-95-95 level.
4. **New infections are falling.** Average incidence fell from 3.51 to 1.38 per 1,000 uninfected adults (2015–2024). Mozambique (10.9 → 4.9) and South Africa (10.1 → 5.2) roughly halved theirs but remain the highest.
5. **About 3.58 million people are still not on treatment.** South Africa accounts for 1.48 million (41%) of them. Ghana (47%) and Egypt (45%) have the lowest ART coverage.
6. **Africa vs USA health spending:** the US spent 16.7% of GDP on health in 2023, compared with an average of 4.9% for the 15 African countries. That is more than three times as much.
7. **Health spending rises with the epidemic.** Across African country-years, health expenditure correlates positively with HIV prevalence (Pearson r ≈ 0.71) but only weakly with ART coverage (r ≈ 0.17). A likely explanation is that heavy epidemics attract more health spending, much of it from donors such as PEPFAR and the Global Fund. Higher spending does not appear to cause higher prevalence.

## Project structure

```
2026-10-12_health_africa_usa_hiv_aids/
├── README.md
├── requirements.txt
├── config/config.yaml              # API, countries, indicators, paths, targets
├── src/
│   ├── ingestion/ingest.py         # World Bank API → data/raw (retry, --dry-run)
│   ├── transformation/transform.py # clean, merge, derive → data/processed
│   └── loading/load.py             # processed CSV → SQLite star schema
├── sql/queries.sql                 # 8 analytical queries
├── notebooks/analysis.ipynb        # EDA + 8 visualisations + findings
├── dags/pipeline_dag.py            # Airflow DAG (Mon/Wed/Fri 06:00)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .github/workflows/ci.yml        # flake8 · py_compile · ingest --dry-run
└── data/
    ├── raw/                        # 5 × CSV + 5 × JSON API responses
    ├── processed/                  # hiv_aids_processed.csv, hiv_aids_summary.csv
    └── hiv_aids.db                 # SQLite database
```

## How to run

### 1. Environment

```bash
cd 2026-10-12_health_africa_usa_hiv_aids
python3.11 -m venv .venv && source .venv/bin/activate
# Airflow 2.8.0 needs sqlalchemy<2.0, so it is installed separately (see step 6)
grep -v '^apache-airflow' requirements.txt > /tmp/req.txt
pip install -r /tmp/req.txt
```

### 2. Run the pipeline

```bash
python src/ingestion/ingest.py --dry-run     # optional: check API connectivity
python src/ingestion/ingest.py               # → data/raw/*.csv, *.json
python src/transformation/transform.py       # → data/processed/*.csv
python src/loading/load.py                   # → data/hiv_aids.db
```

All scripts accept `--config <path>` and `--log-level DEBUG`. You can also override settings with environment variables: `CONFIG_PATH`, `DB_PATH`, `WB_API_BASE_URL`, `API_TIMEOUT`, `LOG_LEVEL`.

### 3. Query with SQL

```bash
sqlite3 -header -column data/hiv_aids.db < sql/queries.sql
```

### 4. Explore the notebook

```bash
jupyter notebook notebooks/analysis.ipynb
```

### 5. Docker

```bash
cd docker
docker compose up --build                                          # full ETL
docker compose run --rm pipeline python src/ingestion/ingest.py --dry-run
# Image only (default CMD runs ingestion):
docker build -f docker/Dockerfile -t hiv-aids-africa-usa:latest ..
```

### 6. Airflow

```bash
python3.11 -m venv .venv-airflow && source .venv-airflow/bin/activate
pip install "apache-airflow==2.8.0" pandas==2.1.4 requests==2.31.0 pyyaml==6.0.1 \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-2.8.0/constraints-3.11.txt
export AIRFLOW_HOME=~/airflow
airflow db migrate
ln -s "$(pwd)/dags/pipeline_dag.py" "$AIRFLOW_HOME/dags/hiv_aids_pipeline_dag.py"
airflow dags test health_africa_usa_hiv_aids_pipeline 2026-10-12
```

The DAG runs `ingest_data → transform_data → load_to_db → generate_report` at 06:00 UTC every Monday, Wednesday and Friday. Each task has 2 retries. The report is written to `data/processed/pipeline_report.txt`.

### 7. CI

`.github/workflows/ci.yml` runs on every push and pull request. It sets up Python 3.11, installs the requirements (without Airflow), runs flake8 on `src/`, runs `py_compile` on every `.py` file, and runs `ingest.py --dry-run` as a smoke test.

## Tech stack

Python 3.11 · pandas · requests + tenacity · PyYAML · SQLite · matplotlib · seaborn · plotly · Jupyter · Apache Airflow 2.8 · Docker · GitHub Actions · flake8

## License & attribution

Data © The World Bank, licensed under [CC BY 4.0](https://datacatalog.worldbank.org/public-licenses#cc-by). The HIV indicators come from UNAIDS estimates.
