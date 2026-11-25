# AI-Powered EdTech & Learning Analytics: Africa and USA Education Data Pipeline

**Domain:** AI / EdTech
**Geographic focus:** Africa (15 countries) and the USA
**Countries:** Nigeria, South Africa, Kenya, Ghana, Ethiopia, Egypt, Tanzania, Uganda, Senegal, Côte d'Ivoire, Cameroon, Zambia, Zimbabwe, Rwanda, Morocco + United States
**Period:** 2015 – 2025 (World Bank Open Data)

---

## Problem Statement

AI tutors, adaptive learning platforms and learning-analytics dashboards promise to personalise education at scale, but they only work where learners can get online, can read, and are inside an education system that collects data. Africa has the world's youngest and fastest-growing population, yet many countries still face low primary completion, limited tertiary capacity and a wide digital divide compared with the USA.

This project builds a reproducible data pipeline that answers:

1. Where is the **digital divide** between African countries and the USA widest, and how fast is it closing?
2. Which countries are most **"EdTech-ready"** — combining internet access, youth literacy and tertiary enrollment?
3. Does higher **education spending** translate into better **primary completion**?
4. How strongly does **internet access** relate to **tertiary participation**?

The outputs help EdTech builders, ministries and funders decide where AI-powered learning tools can be deployed today, and where foundational access must come first.

---

## Data Sources

All data come from the **World Bank Open Data API** (no API key required):

| Indicator | Code | Endpoint |
|---|---|---|
| Primary completion rate (% of relevant age group) | `SE.PRM.CMPT.ZS` | https://api.worldbank.org/v2/country/NG;ZA;KE;GH;ET;EG;TZ;UG;SN;CI;CM;ZM;ZW;RW;MA;US/indicator/SE.PRM.CMPT.ZS?format=json&date=2015:2025&per_page=500 |
| Government expenditure on education (% of GDP) | `SE.XPD.TOTL.GD.ZS` | https://api.worldbank.org/v2/country/NG;ZA;KE;GH;ET;EG;TZ;UG;SN;CI;CM;ZM;ZW;RW;MA;US/indicator/SE.XPD.TOTL.GD.ZS?format=json&date=2015:2025&per_page=500 |
| School enrollment, tertiary (% gross) | `SE.TER.ENRR` | https://api.worldbank.org/v2/country/NG;ZA;KE;GH;ET;EG;TZ;UG;SN;CI;CM;ZM;ZW;RW;MA;US/indicator/SE.TER.ENRR?format=json&date=2015:2025&per_page=500 |
| Individuals using the Internet (% of population) | `IT.NET.USER.ZS` | https://api.worldbank.org/v2/country/NG;ZA;KE;GH;ET;EG;TZ;UG;SN;CI;CM;ZM;ZW;RW;MA;US/indicator/IT.NET.USER.ZS?format=json&date=2015:2025&per_page=500 |
| Literacy rate, youth (% ages 15-24) | `SE.ADT.1524.LT.ZS` | https://api.worldbank.org/v2/country/NG;ZA;KE;GH;ET;EG;TZ;UG;SN;CI;CM;ZM;ZW;RW;MA;US/indicator/SE.ADT.1524.LT.ZS?format=json&date=2015:2025&per_page=500 |

API base: https://api.worldbank.org/v2/ · Documentation: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392

Raw files in `data/raw/` share one schema: `country_id, country_name, indicator_id, indicator_name, year, value`.

**Coverage caveats (from the data itself):** the USA does not report youth literacy; Egypt has no education-expenditure series in the window; Nigeria and Zambia have no primary-completion series; tertiary enrollment is missing for Nigeria, Ethiopia, Zambia and Uganda; Morocco and Kenya have no recent youth-literacy value.

---

## Architecture

```
 ┌──────────────────────────┐
 │  World Bank Open Data    │  5 indicators x 16 countries (2015-2025)
 │  api.worldbank.org/v2    │
 └────────────┬─────────────┘
              │  HTTPS / JSON  (timeout, 3 retries, exponential backoff)
              ▼
 ┌──────────────────────────┐
 │  1. INGESTION            │  src/ingestion/ingest.py
 │  data/raw/wb_*.csv       │  one tidy CSV per indicator
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐
 │  2. TRANSFORMATION       │  src/transformation/transform.py
 │  clean · cast · dedupe   │  nulls dropped, year=int, value=float
 │  pivot · enrich · score  │  edtech_readiness_score, YoY, gap vs USA
 │  data/processed/*.csv    │  education_analytics.csv, country_summary.csv
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐
 │  3. LOADING              │  src/loading/load.py
 │  SQLite                  │  data/education_analytics.db
 │  raw_indicators          │
 │  edtech_readiness        │
 │  country_summary         │
 └────────────┬─────────────┘
              ▼
 ┌──────────────────────────┐      ┌──────────────────────────┐
 │  4. ANALYTICS            │      │  ORCHESTRATION           │
 │  sql/queries.sql (8)     │◄─────┤  Airflow DAG Mon/Wed/Fri │
 │  notebooks/analysis.ipynb│      │  06:00 · Docker · CI     │
 └────────────┬─────────────┘      └──────────────────────────┘
              ▼
 ┌──────────────────────────┐
 │  5. INSIGHTS             │  readiness ranking, digital divide,
 │  plotly charts + report  │  spend vs outcomes, Africa vs USA
 └──────────────────────────┘
```

### EdTech readiness score

```
edtech_readiness_score = weighted mean of available components (0-100)
    internet_users_pct          weight 0.4
    youth_literacy_rate         weight 0.3
    tertiary_enrollment_rate    weight 0.3   (capped at 100)
```

Weights are re-normalised over the components a country reports (`readiness_components` = 1-3). In the country-year table each component uses the most recent value known up to that year. Scores built from 2 components (e.g. USA, Morocco, Kenya) are not strictly comparable with 3-component scores. Weights are configurable in `config/config.yaml`.

---

## Project Structure

```
2026-11-25_ai_africa_usa_edtech_learning_analytics/
├── .github/workflows/ci.yml        # flake8 + import / pipeline smoke tests
├── config/config.yaml              # API, paths, pipeline and logging settings
├── dags/pipeline_dag.py            # Airflow DAG (Mon/Wed/Fri 06:00)
├── data/
│   ├── raw/                        # World Bank CSVs (+ original JSON responses)
│   ├── processed/                  # cleaned & enriched CSVs
│   └── education_analytics.db      # SQLite warehouse (created by load.py)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA + 6 plotly visualisations
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```

---

## How to Run

All commands are run from the project root.

### 1. Set up the environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

(For the pipeline alone, `pip install requests pandas numpy pyyaml` is enough.)

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py            # fetch World Bank data -> data/raw/
python src/ingestion/ingest.py --dry-run  # validate config without calling the API
python src/transformation/transform.py    # -> data/processed/
python src/loading/load.py                # -> data/education_analytics.db
```

### 3. Query the warehouse

```bash
sqlite3 data/education_analytics.db < sql/queries.sql
```

### 4. Explore the notebook

```bash
jupyter notebook notebooks/analysis.ipynb
```

### 5. Run with Docker

```bash
cd docker
docker compose up --build
```

or

```bash
docker build -f docker/Dockerfile -t edtech-learning-analytics .
docker run --rm -v "$(pwd)/data:/app/data" edtech-learning-analytics
```

### 6. Orchestrate with Airflow

```bash
export AIRFLOW_HOME=~/airflow
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/   # or set dags_folder to this project's dags/
airflow dags test edtech_learning_analytics_pipeline 2026-11-25
```

DAG `edtech_learning_analytics_pipeline` runs `ingest_data → transform_data → load_data → generate_report` every Monday, Wednesday and Friday at 06:00 (`0 6 * * 1,3,5`) with 2 retries 5 minutes apart. If you copy the DAG file, keep the project importable (e.g. add the project root to `PYTHONPATH`).

---

## Key Insights

Figures below come from the latest value each country reports in the World Bank data loaded by this pipeline.

| Metric (latest available) | USA | Africa (avg of 15) | Africa range |
|---|---|---|---|
| Internet users (% population) | 94.7 | 46.2 | 8.9 (Uganda) – 91.2 (Morocco) |
| Tertiary enrollment (% gross) | 79.4 | 18.8 | 4.0 (Tanzania) – 48.2 (Morocco) |
| Primary completion (%) | 95.7 | 81.2 | 53.3 (Uganda) – 106.4 (Morocco) |
| Education spend (% GDP) | 5.42 | 3.50 | 0.32 (Nigeria) – 6.16 (Senegal) |
| EdTech readiness score | 88.1 | 50.4 | 24.5 (Kenya*) – 72.8 (Morocco*) |

\* scored on 2 of 3 components because youth literacy is not reported.

1. **The digital divide is closing fast but is still large.** Ghana (+49 pp), Senegal (+38 pp), Egypt (+37 pp) and Morocco (+34 pp) recorded the largest internet-use gains between 2015 and 2024, yet the African median is still about 41% versus 95% in the USA.
2. **The most EdTech-ready African countries** are Morocco, Egypt, South Africa and Ghana (readiness 63–73), making them strong candidates for AI tutoring and online higher-education pilots.
3. **Internet access tracks tertiary participation closely:** Pearson r ≈ 0.79 across 91 country-years (≈ 0.84 for Africa only).
4. **Spending is not the whole story.** Senegal, South Africa and Morocco spend more of GDP on education than the USA, but Senegal's primary completion (~63%) trails far behind; efficiency and delivery, which learning analytics can measure, matter as much as budget.
5. **Foundational gaps persist** in Uganda and Ethiopia (primary completion ~53–54%), where offline-first, low-bandwidth and local-language AI learning tools are most relevant.
6. **Youth literacy is broadly high** (most countries 80–97%), and improved most in Ethiopia (+12.9 pp) and Ghana (+10.9 pp), so the main barrier for digital learning is access rather than reading skills.

---

## Technologies

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Ingestion | requests (timeouts, retries with exponential backoff), PyYAML |
| Transformation | pandas, numpy |
| Storage | SQLite |
| Analytics | SQL (window functions, CTEs), Jupyter, plotly |
| Orchestration | Apache Airflow 2.8 |
| Packaging | Docker, Docker Compose |
| Quality | flake8, GitHub Actions CI |

---

## License & Attribution

Data © The World Bank, licensed under CC BY 4.0 (https://datacatalog.worldbank.org/public-licenses#cc-by).
