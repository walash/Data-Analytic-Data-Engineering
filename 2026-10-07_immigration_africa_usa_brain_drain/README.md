# Brain Drain: Skilled Worker Emigration from Africa to the USA

**Domain:** Immigration  |  **Focus:** Africa to the USA  |  **Date:** 2026-10-07

An end-to-end data engineering project that ingests real **World Bank World Development
Indicators** for 15 major African source countries, models them into a SQLite warehouse and
analyses the drivers and consequences of African brain drain: the emigration of doctors, nurses,
engineers and IT professionals to the United States and other high-income countries.

---

## Problem Statement

Africa trains skilled professionals it then loses. Zimbabwean nurses, Nigerian doctors and software
engineers (the "japa" wave), Kenyan and Ghanaian health workers and Moroccan and Egyptian engineers
increasingly build careers in the USA, the UK, Canada and the Gulf. This project answers:

1. Which countries have the largest net emigration, in absolute terms and per 1,000 residents?
2. How large are the **remittances** that diasporas send home, and how dependent are economies on them?
3. How do **push factors** (unemployment, low GDP per capita) relate to emigration?
4. Do countries with higher **tertiary enrollment** lose more people (education without absorption)?
5. Which countries face the highest combined **Brain Drain Pressure**?

## Countries

Nigeria (NG), Ghana (GH), Kenya (KE), Ethiopia (ET), South Africa (ZA), Egypt (EG), Morocco (MA),
Tanzania (TZ), Uganda (UG), Senegal (SN), Cameroon (CM), Cote d'Ivoire (CI), Zimbabwe (ZW),
Zambia (ZM), Malawi (MW). These are grouped into West, Central, East, North and Southern Africa.

## Data Sources

All data comes from the free, keyless **World Bank Indicators API v2**. Raw responses are stored
unmodified in `data/raw/`.

| Indicator | Code | Raw file | API URL |
|---|---|---|---|
| Net migration | SM.POP.NETM | `wb_net_migration.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/SM.POP.NETM?format=json&per_page=200&mrv=10 |
| Personal remittances received (US$) | BX.TRF.PWKR.CD.DT | `wb_remittances_usd.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/BX.TRF.PWKR.CD.DT?format=json&per_page=200&mrv=10 |
| Personal remittances (% of GDP) | BX.TRF.PWKR.DT.GD.ZS | `wb_remittances_pct_gdp.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/BX.TRF.PWKR.DT.GD.ZS?format=json&per_page=200&mrv=10 |
| GDP per capita (current US$) | NY.GDP.PCAP.CD | `wb_gdp_per_capita.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/NY.GDP.PCAP.CD?format=json&per_page=200&mrv=10 |
| Unemployment (% labour force, ILO) | SL.UEM.TOTL.ZS | `wb_unemployment.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/SL.UEM.TOTL.ZS?format=json&per_page=200&mrv=10 |
| Tertiary enrollment (% gross) | SE.TER.ENRR | `wb_tertiary_enrollment.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/SE.TER.ENRR?format=json&per_page=200&mrv=10 |
| International migrant stock | SM.POP.TOTL | `wb_migrant_stock.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/SM.POP.TOTL?format=json&per_page=200&mrv=5 |
| Population, total | SP.POP.TOTL | `wb_population.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;ZW;ZM;MW/indicator/SP.POP.TOTL?format=json&per_page=200&mrv=5 |

> **Caveat:** World Bank net migration covers all migrants of every skill level and destination.
> It is used as a proxy for brain drain, not as a direct count of skilled emigrants to the USA.

## Architecture

```
 World Bank Indicators API (8 series x 15 countries)
                    |
                    v
 +------------------------------+   data/raw/*.json  (unmodified API responses)
 | 1. INGEST  src/ingestion     |   requests + retry/backoff, pagination, atomic writes
 +------------------------------+
                    |
                    v
 +------------------------------+   data/processed/
 | 2. TRANSFORM src/transform.. |     countries.csv            (dimension + region)
 |    pandas: parse, clean,     |     indicators_long.csv      (country x indicator x year)
 |    pivot, derive metrics     |     country_year_metrics.csv (wide panel + per-capita rates)
 +------------------------------+     brain_drain_index.csv    (latest snapshot + index)
                    |
                    v
 +------------------------------+   data/brain_drain.db (SQLite)
 | 3. LOAD  src/loading         |     dim_country, fact_indicator, country_year_metrics,
 |    DDL, PK/FK, indexes, audit|     brain_drain_index, pipeline_runs
 +------------------------------+
          |                  |
          v                  v
  sql/queries.sql     notebooks/analysis.ipynb      reports/ (Airflow generate_report)
  (12 analytical      (9 matplotlib/seaborn
   queries)            visualisations)

 Orchestration: Airflow DAG  ingest -> transform -> load -> generate_report  (@monthly)
 Packaging:     Docker image + docker-compose (pipeline + sqlite-web database UI)
 CI:            GitHub Actions - flake8 lint + ingestion/ETL smoke test
```

### Derived metrics

| Metric | Definition |
|---|---|
| `net_migration_per_1000` | net migration / population x 1,000 (population carried to nearest observed year) |
| `remittances_per_capita_usd` | remittances (US$) / population |
| `migrant_stock_pct_pop` | international migrant stock / population x 100 |
| `gdp_per_capita_growth_pct` | year-over-year % change in GDP per capita |
| `brain_drain_index` | 0-100 weighted score: 30% unemployment, 30% GDP-per-capita gap, 20% net outflow rate, 20% remittance dependence (min-max normalised, weights in `config/config.yaml`) |

## How to Run

### 1. Set up the environment

```bash
cd 2026-10-07_immigration_africa_usa_brain_drain
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

No API key is needed. `config/config.yaml` contains labeled `YOUR_API_KEY_HERE` placeholders that
are only for future keyed sources.

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run          # validate config and URLs, no network
python src/ingestion/ingest.py                    # refresh data/raw/ from the World Bank API
python src/transformation/transform.py            # data/raw -> data/processed
python src/loading/load.py                        # data/processed -> data/brain_drain.db
```

Useful options: `ingest.py --indicators net_migration unemployment`, `--output-dir`;
`transform.py --raw-dir/--out-dir`; `load.py --db path/to.db`.

### 3. Explore the results

```bash
sqlite3 -header -column data/brain_drain.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build
# pipeline container runs ingest -> transform -> load, then exits
# database UI (sqlite-web) at http://localhost:8080
```

Or run only the pipeline image:

```bash
docker build -f docker/Dockerfile -t africa-usa-brain-drain:latest .
docker run --rm -v "$(pwd)/data:/app/data" africa-usa-brain-drain:latest
```

### 5. Orchestrate with Airflow

```bash
pip install "apache-airflow==2.10.2" --constraint \
  "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.2/constraints-3.11.txt"
export AIRFLOW_HOME=~/airflow
mkdir -p $AIRFLOW_HOME/dags && cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/  # or symlink the project
airflow standalone
```

DAG `africa_usa_brain_drain_pipeline` runs monthly: `ingest -> transform -> load -> generate_report`.
The final task writes `reports/brain_drain_report_<date>.md`. If you copy the DAG file instead of
symlinking it, export `BRAIN_DRAIN_PROJECT_ROOT=/path/to/2026-10-07_immigration_africa_usa_brain_drain`.

## Key Insights (from the current World Bank data)

- **Largest net emigration, 2016-2025:** Zimbabwe (about -969k people), Morocco (-544k),
  Kenya (-413k) and Nigeria (-283k). Together they make up about 90% of the panel's total net outflow.
- **South Africa is a net receiver** (+1.7M) because it is a destination for regional migrants,
  even though it loses its own skilled professionals to the UK, Australia and the USA. South Africa
  also has the highest unemployment in the panel (over 32%).
- **Remittances rose from about US$57bn (2016) to US$89bn (2024)** across the 15 countries.
  Egypt (US$41.5bn in 2025) and Nigeria (US$22.8bn) lead in absolute terms. Egypt, Senegal and
  Zimbabwe depend most on remittances (about 8-11% of GDP).
- **The income gap is the main pull.** GDP per capita ranges from about US$670 (Malawi) to
  US$6,600 (South Africa), a small fraction of US levels.
- **Education without absorption:** Morocco (48% gross tertiary enrollment) and Egypt (38%) pair
  high enrollment with net emigration.
- **Brain Drain Pressure Index (top 4):** Zimbabwe, Senegal, Egypt and Nigeria.

## Technologies

Python 3.11, pandas, NumPy, requests, PyYAML, SQLite, matplotlib, seaborn, Jupyter,
Apache Airflow, Docker / Docker Compose, sqlite-web, GitHub Actions, flake8.

## Project Structure

```
2026-10-07_immigration_africa_usa_brain_drain/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml              # API URLs, indicators, regions, paths, index weights
├── data/
│   ├── raw/                     # World Bank API responses (8 JSON files)
│   ├── processed/               # tidy CSVs produced by transform.py
│   └── brain_drain.db           # SQLite warehouse produced by load.py
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/queries.sql              # 12 analytical queries
├── notebooks/analysis.ipynb     # 9 visualisations + insights
├── dags/pipeline_dag.py         # Airflow DAG
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/workflows/ci.yml     # flake8 + smoke test
```
