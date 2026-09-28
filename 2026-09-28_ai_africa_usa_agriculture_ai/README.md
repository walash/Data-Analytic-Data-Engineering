# AI in Agriculture: Crop Yield Prediction & Food Security Analytics — Africa & USA

> An end-to-end data engineering project that ingests real World Bank
> agricultural data, engineers AI/analytics features, and surfaces
> food-security and investment-priority insights for 15 African countries and
> the United States.

---

## Overview

| | |
|---|---|
| **Domain** | Artificial Intelligence / Agriculture |
| **Geographic focus** | 15 African countries + USA |
| **Data source** | [World Bank Open Data API](https://data.worldbank.org/) |
| **Stack** | Python, Pandas, Scikit-learn, SQLite, Apache Airflow, Docker, Plotly, Jupyter |

### Countries analysed

**Africa (15):** Nigeria, Ghana, Kenya, Ethiopia, South Africa, Egypt,
Morocco, Tanzania, Uganda, Senegal, Cameroon, Côte d'Ivoire, Rwanda, Zambia,
Mozambique.
**Reference economy:** United States.

---

## Problem Statement

How can AI and data engineering help **predict crop yields**, **identify food
security risks**, and **guide agricultural investment** across Africa and the
USA?

Agriculture is the economic backbone of much of Africa, yet cereal yields lag
far behind high-input economies such as the USA. By consolidating trusted
World Bank indicators into a clean analytical warehouse and layering
AI-oriented features on top (yield growth, fertilizer efficiency, a composite
food-security score and an AI-investment-priority ranking), this project makes
it possible to answer questions such as:

- Which countries have the highest / lowest cereal productivity, and why?
- Where is fertilizer being used most (and least) efficiently?
- Which countries are most food-insecure and should be prioritised for
  AI-driven agricultural investment?
- How large is the Africa–USA productivity gap, and how is it changing?
- Can a simple ML model predict cereal yield from fertilizer use and
  agricultural land share?

---

## Data Sources

All data comes from the **World Bank Open Data API** (`https://api.worldbank.org/v2`).
One indicator is fetched per country cohort:

| Indicator | Code | API endpoint (Africa cohort) |
|---|---|---|
| Cereal yield (kg/hectare) | `AG.YLD.CREL.KG` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/AG.YLD.CREL.KG?format=json` |
| Agricultural land (% of land area) | `AG.LND.AGRI.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/AG.LND.AGRI.ZS?format=json` |
| Food production index (2014–16 = 100) | `AG.PRD.FOOD.XD` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/AG.PRD.FOOD.XD?format=json` |
| Fertilizer consumption (kg/hectare) | `AG.CON.FERT.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/AG.CON.FERT.ZS?format=json` |
| Agriculture value added (% of GDP) | `NV.AGR.TOTL.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/NV.AGR.TOTL.ZS?format=json` |
| Rural population (% of total) | `SP.RUR.TOTL.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ/indicator/SP.RUR.TOTL.ZS?format=json` |

The same six indicators are fetched for the USA by replacing the country list
with `US` (e.g.
`https://api.worldbank.org/v2/country/US/indicator/AG.YLD.CREL.KG?format=json`).

---

## Architecture

```
                    ┌──────────────────────────┐
                    │   World Bank Open Data    │
                    │           API             │
                    └────────────┬─────────────┘
                                 │  HTTP (requests + retry/backoff)
                                 ▼
                    ┌──────────────────────────┐
                    │        INGESTION          │   src/ingestion/ingest.py
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │   RAW DATA  (JSON)        │   data/raw/*.json
                    └────────────┬─────────────┘
                                 │  parse • merge • clean • feature-engineer
                                 ▼
                    ┌──────────────────────────┐
                    │      TRANSFORMATION       │   src/transformation/transform.py
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │  PROCESSED DATA (CSV)     │   data/processed/*.csv
                    └────────────┬─────────────┘
                                 │  load
                                 ▼
                    ┌──────────────────────────┐
                    │        SQLite DB          │   data/agriculture_analytics.db
                    │  agriculture_metrics      │   src/loading/load.py
                    │  country_summary          │
                    └───────┬───────────┬──────┘
                            │           │
              ┌─────────────▼──┐   ┌────▼────────────────┐
              │  Analysis      │   │   Airflow DAG        │
              │  Notebook +    │   │ (ingest→transform→   │
              │  SQL queries   │   │  load→report→        │
              │                │   │  validate)           │
              └────────────────┘   └─────────────────────┘
```

---

## How to Run

### Option A — Local (Python)

```bash
# 1. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Ingest raw data from the World Bank API (writes data/raw/*.json)
python src/ingestion/ingest.py

# 3. Transform + feature-engineer (writes data/processed/*.csv)
python src/transformation/transform.py

# 4. Load into SQLite (creates data/agriculture_analytics.db)
python src/loading/load.py

# 5. Explore results
sqlite3 data/agriculture_analytics.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

> Raw World Bank JSON files are already provided in `data/raw/`, so you can skip
> step 2 and run the transformation directly against the bundled data.

### Option B — Docker

```bash
# Build and run the full ETL pipeline (ingest -> transform -> load)
docker compose -f docker/docker-compose.yml up --build pipeline

# Bring up Postgres + the Airflow web UI (http://localhost:8080, admin/admin)
docker compose -f docker/docker-compose.yml up -d postgres airflow-webserver
```

### Option C — Airflow orchestration

Place `dags/pipeline_dag.py` in your Airflow `dags/` folder (or use the Docker
stack above). The DAG `agriculture_ai_pipeline` runs on a
**Mon/Wed/Fri 06:00** schedule and chains:
`ingest → transform → load → generate_report → validate`.

---

## Key Insights — AI in African Agriculture

- **Massive productivity gap.** USA cereal yields are several times higher than
  the African cohort average, driven largely by far greater fertilizer
  intensity and mechanisation — a gap AI-guided precision agriculture is well
  positioned to narrow.
- **Fertilizer efficiency, not just volume, matters.** Some African countries
  achieve strong yield per kg of fertilizer, showing that smarter,
  data-driven input allocation can lift output without proportional cost.
- **Food-security risk is concentrated.** The composite food-security score
  cleanly separates a "Critical" cohort of low-yield, high-rural-population
  countries that should be prioritised for AI-driven interventions.
- **High agricultural dependence ≠ high productivity.** Countries where
  agriculture is a large share of GDP often still post below-average yields,
  underscoring structural under-investment that AI-informed policy can target.
- **Yield momentum is uneven.** Year-over-year yield growth varies widely by
  country and year, so AI forecasting models must capture country-specific
  volatility rather than assume a single continental trend.

---

## Technologies

- **Python** — pipeline implementation language
- **Pandas / NumPy** — data wrangling and feature engineering
- **Scikit-learn** — cereal-yield prediction model (Linear Regression)
- **SQLite** — analytical warehouse
- **Apache Airflow** — pipeline orchestration & scheduling
- **Docker / Docker Compose** — reproducible, portable execution
- **Plotly / Matplotlib / Seaborn** — interactive & static visualisation
- **Jupyter** — exploratory analysis notebook

---

## Project Structure

```
.
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/                    # World Bank JSON (provided)
│   └── processed/              # generated CSVs
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── sql/
│   └── queries.sql
├── notebooks/
│   └── analysis.ipynb
├── dags/
│   └── pipeline_dag.py
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/
    └── workflows/ci.yml
```
