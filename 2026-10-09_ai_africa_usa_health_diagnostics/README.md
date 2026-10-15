# AI-Powered Healthcare Diagnostics: Bridging the Africa-USA Health Gap

| | |
|---|---|
| **Domain** | AI / Health Tech |
| **Geographic focus** | Sub-Saharan Africa (plus Egypt and Morocco in North Africa) and the USA |
| **Countries** | Nigeria, Ghana, Kenya, Ethiopia, South Africa, Egypt, Morocco, Tanzania, Uganda, Senegal, Cameroon, Côte d'Ivoire, Rwanda, Zambia, United States |
| **Data** | World Bank World Development Indicators, 2014-2024 (real API data) |
| **Pipeline** | Python ETL → SQLite → SQL analytics → Jupyter + scikit-learn, orchestrated by Airflow and containerised with Docker |

---

## Problem statement

A diagnosis comes before every treatment. Diagnostic capacity depends on clinicians who can read symptoms, scans and lab results, on facilities to work in, and on money to pay for both. The 14 African countries in this project have on average **about 0.3 physicians per 1,000 people, compared with 3.7 in the USA**. They spend **roughly 1/100 as much per person on health**, and their children die before age five at **more than seven times** the US rate.

Training enough doctors to close that gap would take decades. AI-assisted diagnostics (clinical decision support for community health workers, AI-read chest X-rays, smartphone microscopy, AI-guided ultrasound) can multiply the reach of the clinicians who already work there. This project builds a reproducible data pipeline that:

1. Quantifies the Africa-USA gap in health spending, physician density, hospital capacity and outcomes.
2. Scores each country's existing **healthcare access** (0-100) and **AI readiness**.
3. Fits a machine-learning model linking system inputs to life expectancy and simulates the effect of AI-boosted clinical capacity.
4. Identifies where AI diagnostics could have the largest impact.

---

## Data sources

All data comes from the free, keyless **World Bank Indicators API v2**. No API key is required.

| Indicator | Code | Raw file | API endpoint |
|---|---|---|---|
| Current health expenditure per capita (current US$) | `SH.XPD.CHEX.PC.CD` | `health_expenditure_per_capita.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US/indicator/SH.XPD.CHEX.PC.CD?format=json&per_page=500 |
| Physicians (per 1,000 people) | `SH.MED.PHYS.ZS` | `physicians_per_1000.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US/indicator/SH.MED.PHYS.ZS?format=json&per_page=500 |
| Mortality rate, under-5 (per 1,000 live births) | `SH.DYN.MORT` | `under5_mortality.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US/indicator/SH.DYN.MORT?format=json&per_page=500 |
| Hospital beds (per 1,000 people) | `SH.MED.BEDS.ZS` | `hospital_beds_per_1000.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US/indicator/SH.MED.BEDS.ZS?format=json&per_page=500 |
| Life expectancy at birth, total (years) | `SP.DYN.LE00.IN` | `life_expectancy.json` | https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US/indicator/SP.DYN.LE00.IN?format=json&per_page=500 |

Raw coverage in `data/raw/`: 150 records per indicator. The non-null counts are 150 (expenditure), 92 (physicians), 150 (under-5 mortality), 49 (hospital beds) and 150 (life expectancy). Documentation: https://datahelpdesk.worldbank.org/knowledgebase/articles/889392

---

## Architecture

```
 ┌────────────────────────┐
 │  World Bank API v2     │  5 indicators x 15 countries (JSON)
 │  (raw data source)     │
 └───────────┬────────────┘
             │  HTTPS GET, 3 retries + exponential backoff
             ▼
 ┌────────────────────────┐
 │  1. INGESTION          │  src/ingestion/ingest.py
 │  data/raw/*.json       │  raw World Bank payloads, unchanged
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐
 │  2. TRANSFORMATION     │  src/transformation/transform.py
 │  parse → pivot wide →  │  dedupe, cast types, forward-fill per country,
 │  clean → derive        │  africa_flag, healthcare_access_score (0-100)
 │  data/processed/*.csv  │
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐
 │  3. LOADING            │  src/loading/load.py
 │  SQLite                │  health_indicators | country_summary |
 │  health_diagnostics.db │  yearly_trends
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐
 │  4. ANALYSIS           │  sql/queries.sql (8 queries, window functions)
 │                        │  notebooks/analysis.ipynb (EDA, 5+ charts)
 └───────────┬────────────┘
             ▼
 ┌────────────────────────┐
 │  5. AI INSIGHTS        │  scikit-learn regression, scenario simulation,
 │                        │  AI readiness score, recommendations
 └────────────────────────┘

 Orchestration: Airflow DAG `health_diagnostics_pipeline` (Mon/Wed/Fri 06:00)
 Packaging:     Docker / docker-compose     CI: GitHub Actions (flake8 + py_compile)
```

---

## Project structure

```
2026-10-09_ai_africa_usa_health_diagnostics/
├── README.md
├── requirements.txt
├── config/config.yaml              # API URL, countries, indicators, paths, retries
├── data/
│   ├── raw/                        # World Bank JSON (5 files)
│   ├── processed/                  # health_diagnostics_processed.csv
│   └── health_diagnostics.db       # SQLite output
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

### Processed schema (`health_diagnostics_processed.csv` / `health_indicators`)

| Column | Description |
|---|---|
| `country_code` | ISO-2 code (e.g. `NG`, `US`) |
| `country_name` | World Bank country name |
| `year` | 2014-2024 |
| `health_expenditure_pc` | Current health expenditure per capita, US$ |
| `physicians_per_1000` | Physicians per 1,000 people |
| `under5_mortality` | Under-5 deaths per 1,000 live births |
| `hospital_beds_per_1000` | Hospital beds per 1,000 people |
| `life_expectancy` | Life expectancy at birth, years |
| `africa_flag` | 1 = African country, 0 = United States |
| `healthcare_access_score` | 0-100: mean of min-max normalised physicians, beds and log expenditure (available components only) |

Sparse indicators are forward-filled within each country, so the most recent survey value carries forward to later years.

---

## How to run

### 1. Local (Python 3.11)

```bash
cd 2026-10-09_ai_africa_usa_health_diagnostics
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 src/ingestion/ingest.py            # fetch World Bank data -> data/raw/
python3 src/ingestion/ingest.py --dry-run  # (optional) print the API URLs only
python3 src/transformation/transform.py    # -> data/processed/health_diagnostics_processed.csv
python3 src/loading/load.py                # -> data/health_diagnostics.db
```

### 2. SQL analysis

```bash
sqlite3 data/health_diagnostics.db < sql/queries.sql
```

### 3. Notebook

```bash
jupyter notebook notebooks/analysis.ipynb
```

### 4. Docker

```bash
cd docker
docker compose up --build        # runs ingest -> transform -> load; outputs persist in ../data
```

### 5. Airflow

```bash
export AIRFLOW_HOME=~/airflow
export HEALTH_DIAGNOSTICS_HOME=$(pwd)
airflow db init
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
airflow dags test health_diagnostics_pipeline 2026-10-09
```

The DAG runs `ingest_data → transform_data → load_data → generate_report` every Monday, Wednesday and Friday at 06:00 (`0 6 * * 1,3,5`), with 2 retries 5 minutes apart. The report is written to `data/reports/health_gap_report_<date>.txt`.

Configuration lives in `config/config.yaml`. These environment variables override paths: `CONFIG_PATH`, `RAW_DATA_PATH`, `PROCESSED_FILE`, `DB_PATH`, `LOG_LEVEL`.

---

## Key insights

All figures are computed from the World Bank data in this repo. Regional "Africa" values are simple averages of the 14 African countries.

| Indicator (2022) | Africa avg | USA | Gap |
|---|---|---|---|
| Health expenditure per capita | **US$119** | **US$12,586** | ~106x |
| Physicians per 1,000 | **0.30** | **3.68** | ~12x |
| Hospital beds per 1,000 | **1.01** | **2.68** | ~2.7x |
| Under-5 mortality per 1,000 births | **49.8** | **6.5** | ~7.7x |
| Life expectancy (years) | **65.8** | **77.4** | 11.7 years |

1. **Physicians are the biggest structural gap.** Ethiopia (0.11), Rwanda (0.09) and Senegal (0.12) have roughly one doctor per 10,000 people. The bed gap is much smaller (~2.7x), so the shortage is in **diagnostic labour** more than in buildings. This is where AI decision support helps most.
2. **Spending is about 100x lower.** South Africa, the highest African spender (US$570 per person in 2022), still spends about 1/22 of the US level. In 2023 the USA-to-Africa ratio was about 116x.
3. **Child mortality remains high where diagnosis is scarce.** In 2024 Nigeria recorded 115.6 under-5 deaths per 1,000 births and Cameroon and Côte d'Ivoire about 65, against 6.5 in the USA. Physician density is negatively correlated with under-5 mortality (Spearman ρ ≈ -0.46).
4. **Progress is measurable.** From 2015 to 2024, under-5 mortality fell the most in Cameroon (-23.7), Côte d'Ivoire (-21.0), Ethiopia (-19.9), Tanzania (-17.3) and Ghana (-16.6 per 1,000).
5. **Money does not translate into outcomes automatically.** Nigeria raised health spending in 2019-2021 while under-5 mortality did not improve (SQL Q7).
6. **The ML model points to clinical capacity.** In a linear regression of life expectancy on log health expenditure and physician density (R² ≈ 0.27 on held-out data), each additional physician per 1,000 people is associated with about **+5.4 years** of life expectancy. Spending adds little once physicians are controlled for. A simulated 50% AI-driven boost in effective physician capacity is associated with +0.2 to +2.1 years across African countries.
7. **AI readiness.** Morocco, Egypt and South Africa score highest among African countries on the composite AI readiness score (SQL Q8). They are good pilot markets. Nigeria, Côte d'Ivoire and Cameroon have the highest diagnostic need.

### AI recommendations
- Deploy **AI-assisted triage and clinical decision support** for community health workers in high-mortality, low-physician countries.
- Scale **AI-read imaging and point-of-care diagnostics** (TB chest X-ray, malaria microscopy, obstetric ultrasound) to reduce reliance on scarce specialists.
- Pilot in **readiness-adjusted markets** (Morocco, Egypt, South Africa, Kenya), then expand.
- Invest in **health-data infrastructure**: only 49 of 150 hospital-bed observations are reported, and fair AI needs reliable local data.

*Limitations:* the analysis is observational and covers 15 countries. Forward-filled values and a linear model show associations, not causal effects.

---

## Technologies

| Layer | Tools |
|---|---|
| Language | Python 3.11 |
| Data processing | Pandas, NumPy |
| Machine learning | Scikit-learn |
| Storage | SQLite (SQL with window functions) |
| Visualisation | Matplotlib, Seaborn, Plotly, Jupyter |
| Orchestration | Apache Airflow 2.7 |
| Packaging | Docker, docker-compose |
| CI | GitHub Actions (flake8, py_compile) |
| Config | PyYAML |

---

## License and attribution

The data is © The World Bank, licensed under [CC BY 4.0](https://datacatalog.worldbank.org/public-licenses#cc-by). Code is provided for educational and portfolio purposes.
