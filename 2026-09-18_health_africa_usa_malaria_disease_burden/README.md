# Malaria & Infectious Disease Burden: Africa vs USA — A Data Engineering Perspective

**Domain:** Health Sector | **Geographic Focus:** Sub-Saharan Africa & United States

---

## 1. Problem Statement

Malaria remains one of the most persistent public-health challenges of our time, and its
burden is distributed with stark geographic inequality. Sub-Saharan Africa carries the
overwhelming majority of the world's malaria incidence and deaths, while high-income
countries such as the United States report effectively zero endemic transmission. This
project builds an end-to-end data-engineering pipeline to quantify that disparity and to
explore how it relates to broader health-system indicators — health expenditure as a share
of GDP, life expectancy, and under-5 mortality.

Specifically, the pipeline is designed to answer questions such as:

- Which African countries carry the highest malaria incidence and death burden?
- How has malaria incidence trended between 2010 and 2024 across the focus countries?
- Is there an observable inverse relationship between health expenditure and malaria burden?
- How large is the life-expectancy and under-5 mortality gap between Sub-Saharan Africa
  and the United States, and how is it changing over time?

## 2. Real Data Sources

All data is fetched from **public, no-key** APIs. No credentials are required or stored.

| Dataset | Source | Endpoint |
|---------|--------|----------|
| Malaria estimated incidence (per 1,000 at risk) | WHO Global Health Observatory | `https://ghoapi.azureedge.net/api/MALARIA_EST_INCIDENCE` |
| Malaria estimated deaths | WHO Global Health Observatory | `https://ghoapi.azureedge.net/api/MALARIA_EST_DEATHS` |
| Current health expenditure (% of GDP) | World Bank | `https://api.worldbank.org/v2/country/{iso}/indicator/SH.XPD.CHEX.GD.ZS?format=json&per_page=200&mrv=10` |
| Life expectancy at birth (years) | World Bank | `https://api.worldbank.org/v2/country/{iso}/indicator/SP.DYN.LE00.IN?format=json&per_page=200&mrv=10` |
| Under-5 mortality (per 1,000 live births) | World Bank | `https://api.worldbank.org/v2/country/{iso}/indicator/SH.DYN.MORT?format=json&per_page=200&mrv=10` |

**African focus countries (ISO-3):** KEN, ETH, NGA, GHA, ZAF, UGA, TZA, MOZ, ZMB, MWI
(Kenya, Ethiopia, Nigeria, Ghana, South Africa, Uganda, Tanzania, Mozambique, Zambia, Malawi)
plus **USA** as the high-income comparator.

## 3. Architecture

```
 ┌────────────────────────────────────────────────────────────────────────┐
 │                          RAW DATA SOURCES                               │
 │   WHO GHO API              World Bank API                               │
 │   • MALARIA_EST_INCIDENCE  • SH.XPD.CHEX.GD.ZS (health exp % GDP)       │
 │   • MALARIA_EST_DEATHS     • SP.DYN.LE00.IN    (life expectancy)        │
 │                            • SH.DYN.MORT       (under-5 mortality)      │
 └───────────────────────────────────┬────────────────────────────────────┘
                                      │  (requests + HTTPAdapter/Retry)
                                      ▼
                          ┌───────────────────────┐
                          │  INGESTION            │  src/ingestion/ingest.py
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  data/raw/  (5 CSVs)  │
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  TRANSFORMATION       │  src/transformation/transform.py
                          │  clean • enrich •     │
                          │  categorise • merge   │
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  data/processed/      │  malaria_master.csv
                          │                       │  health_indicators.csv
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  LOADING              │  src/loading/load.py
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  SQLite DB            │  data/health_disease_burden.db
                          │  malaria_incidence    │
                          │  malaria_deaths       │
                          │  health_indicators    │
                          │  country_metadata     │
                          └───────────┬───────────┘
                                      ▼
                          ┌───────────────────────┐
                          │  ANALYSIS             │  notebooks/analysis.ipynb
                          │  + sql/queries.sql    │
                          └───────────────────────┘

 Orchestration: dags/pipeline_dag.py (Apache Airflow, @weekly)
 Packaging:     docker/Dockerfile + docker/docker-compose.yml
 CI:            .github/workflows/ci.yml (flake8 + smoke test)
```

## 4. How to Run

### 4.1 Local (stage by stage)

```bash
# 1. Create environment and install dependencies
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Ingest raw data from WHO GHO + World Bank APIs -> data/raw/
python -m src.ingestion.ingest

# 3. Clean, enrich and merge -> data/processed/
python -m src.transformation.transform

# 4. Load processed CSVs into SQLite -> data/health_disease_burden.db
python -m src.loading.load

# 5. Explore
sqlite3 data/health_disease_burden.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4.2 Docker

```bash
cd docker
docker compose up --build
# Runs ingest -> transform -> load inside the container,
# writing outputs to the mounted ./data volume.
```

### 4.3 Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder. The DAG
`health_disease_burden_pipeline` runs weekly and chains
`ingest_data → transform_data → load_to_db → generate_report`.

## 5. Key Insights

Insights below reflect the real data patterns observed in the fetched datasets:

- **Africa carries the overwhelming majority of the global malaria burden.** The WHO
  estimates that the African region accounts for **95%+** of malaria cases and deaths
  worldwide, while the United States reports no endemic transmission.
- **Incidence is concentrated in a handful of high-burden countries.** Nigeria,
  Mozambique and Uganda show persistently high incidence per 1,000 population at risk,
  whereas countries with stronger control programmes (e.g. South Africa) sit far lower.
- **Inverse relationship between health expenditure and malaria burden.** Countries that
  spend a larger share of GDP on health tend to show lower malaria incidence and lower
  under-5 mortality — consistent with the protective role of sustained health investment.
- **Large and slow-closing life-expectancy gap.** Life expectancy in the focus African
  countries trails the United States by a wide margin, and under-5 mortality remains
  many times higher in Sub-Saharan Africa than in the USA.

## 6. Technologies

- **Python 3.11**
- **pandas / numpy** — data cleaning and transformation
- **requests** (with `HTTPAdapter` + `Retry`) — resilient API ingestion
- **SQLite** — analytical data store
- **SQLAlchemy** — DB helper
- **Apache Airflow** — pipeline orchestration
- **Docker / Docker Compose** — reproducible packaging
- **Jupyter** — interactive analysis
- **matplotlib / seaborn / plotly** — visualization
- **GitHub Actions** — CI (flake8 lint + smoke test)

## 7. Project Structure

```
2026-09-18_health_africa_usa_malaria_disease_burden/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/                     # 5 source CSVs (WHO GHO + World Bank)
│   └── processed/               # malaria_master.csv, health_indicators.csv
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
└── .github/workflows/ci.yml
```
