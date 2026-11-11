# AI-Driven Climate & Environmental Monitoring: Africa and USA

| | |
|---|---|
| **Domain** | AI / Environmental Data Engineering |
| **Geographic focus** | Africa (29 African countries) and USA — 30 countries in total |
| **Data source** | World Bank Open Data API (5 indicators, 2013-2025) |
| **Stack** | Python, pandas, scikit-learn, SQLite, Apache Airflow, Docker, Plotly |
| **Date** | 2026-11-11 |

---

### Problem Statement

Climate risk in Africa and the USA is shaped by very different starting points: much of Sub-Saharan Africa relies
on traditional biomass for energy, is losing forest to agricultural expansion and still lacks universal electricity
access, while the USA has universal access, high per-capita consumption and a slowly growing renewable share.
Policy makers, climate-finance analysts and NGOs need a **reproducible, automatically refreshed data pipeline** that:

1. Collects comparable climate and energy indicators for both regions from an authoritative open source.
2. Cleans and harmonises them into an analysis-ready warehouse.
3. Engineers monitoring features (renewable trend, forest-loss rate, access change).
4. Applies **machine learning** (scikit-learn `LinearRegression`) to forecast renewable energy share for 2025-2027.
5. Surfaces insights through SQL and interactive Plotly visualisations.

Key questions answered:

- Which countries are growing their renewable share fastest, and which are moving backwards?
- How does forest loss in Africa compare with the USA?
- Is a high renewable share a sign of clean energy, or of energy poverty?
- Which countries remain below 50% electricity access?
- Where is renewable share projected to be in 2027 if current trends continue?

---

### Data Sources

All data come from the **World Bank Open Data API** (no API key required).
`{countries}` = `US;NG;KE;ZA;ET;GH;EG;TZ;UG;SN;CM;CI;MZ;ZM;ZW;AO;SD;ML;BF;NE;RW;MG;BJ;TG;GN;SL;LR;GM;GW;CV`

| Indicator | Code | Endpoint | Raw file | Rows |
|---|---|---|---|---|
| Forest area (% of land area) | `AG.LND.FRST.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/AG.LND.FRST.ZS?format=json | `forest_area.csv` | 300 |
| Renewable energy consumption (% of total final energy consumption) | `EG.FEC.RNEW.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/EG.FEC.RNEW.ZS?format=json | `renewable_energy.csv` | 281 |
| Agricultural land (% of land area) | `AG.LND.AGRI.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/AG.LND.AGRI.ZS?format=json | `agricultural_land.csv` | 300 |
| Electric power consumption (kWh per capita) | `EG.USE.ELEC.KH.PC` | https://api.worldbank.org/v2/country/{countries}/indicator/EG.USE.ELEC.KH.PC?format=json | `electric_power_consumption.csv` | 208 |
| Access to electricity (% of population) | `EG.ELC.ACCS.ZS` | https://api.worldbank.org/v2/country/{countries}/indicator/EG.ELC.ACCS.ZS?format=json | `electricity_access.csv` | 300 |

Ingestion adds `per_page=1000&mrv=10` (most recent 10 values). Each raw CSV has the columns
`country_code, country_name, indicator_id, indicator_name, year, value`.

**Countries:** USA, Nigeria, Kenya, South Africa, Ethiopia, Ghana, Egypt, Tanzania, Uganda, Senegal, Cameroon,
Côte d'Ivoire, Mozambique, Zambia, Zimbabwe, Angola, Sudan, Mali, Burkina Faso, Niger, Rwanda, Madagascar, Benin,
Togo, Guinea, Sierra Leone, Liberia, Gambia, Guinea-Bissau, Cape Verde.

**Coverage notes:** renewable energy runs 2013-2022 (latest point 2021 or 2022 depending on the country); forest,
agricultural land and access run 2015-2024; electric power consumption runs 2016-2025 and is unavailable for
Cape Verde, Guinea, Gambia, Guinea-Bissau, Liberia, Mali and Sierra Leone.

---

### Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │                 World Bank Open Data API  (api.worldbank.org/v2)         │
 │  AG.LND.FRST.ZS │ EG.FEC.RNEW.ZS │ AG.LND.AGRI.ZS │ EG.USE.ELEC.KH.PC │  │
 │                              EG.ELC.ACCS.ZS                              │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     │  HTTPS / JSON, paginated,
                                     │  3 retries + exponential backoff
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  1. INGESTION   src/ingestion/ingest.py          ──►  data/raw/*.csv     │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  2. TRANSFORMATION + ML   src/transformation/transform.py                │
 │     clean ─► normalise names ─► dedupe ─► pivot wide ─► features         │
 │     (renewable_energy_trend, forest_loss_rate, ...)                      │
 │     scikit-learn LinearRegression per country ─► 2025-2027 forecasts     │
 │                                                  ──►  data/processed/    │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  3. LOADING   src/loading/load.py   ──►  data/climate_monitoring.db      │
 │     climate_indicators │ country_profiles │ ml_predictions  (SQLite)     │
 └──────────────┬────────────────────────────┬──────────────────────────────┘
                ▼                            ▼
 ┌──────────────────────────┐   ┌───────────────────────────────────────────┐
 │ sql/queries.sql (8 SQL)  │   │ notebooks/analysis.ipynb (Plotly charts)  │
 └──────────────────────────┘   └───────────────────────────────────────────┘

 Orchestration: Airflow DAG `climate_monitoring_pipeline` (Mon/Wed/Fri 06:00)
   ingest_data ─► transform_data ─► load_data ─► generate_report
 Packaging: Docker (pipeline + Adminer)     CI: GitHub Actions (flake8 + dry-run)
```

---

### Project Structure

```
2026-11-11_ai_africa_usa_climate_environmental_monitoring/
├── README.md
├── requirements.txt
├── config/config.yaml              # API, countries, indicators, paths, ML, logging
├── data/
│   ├── raw/                        # 5 World Bank CSVs
│   ├── processed/                  # long, wide, profiles, ml_predictions CSVs
│   └── climate_monitoring.db       # SQLite warehouse (created by load.py)
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

### Data Model

| Table | Grain | Key columns |
|---|---|---|
| `climate_indicators` | country × year | 5 indicators, `renewable_energy_trend`, `forest_loss_rate`, `electricity_access_change`, `agricultural_land_change` |
| `country_profiles` | country | latest value + year + change for each indicator, `avg_forest_loss_rate`, region / sub-region |
| `ml_predictions` | country × forecast year | `predicted_renewable_energy_pct`, `trend_slope_pp_per_year`, `r2_score`, `mae` |

---

### How to Run

#### 1. Set up the environment

```bash
cd 2026-11-11_ai_africa_usa_climate_environmental_monitoring
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`apache-airflow` is only needed for orchestration. For a lightweight local run:

```bash
pip install pandas==2.1.4 numpy==1.26.4 requests==2.31.0 scikit-learn==1.4.2 pyyaml==6.0.1 plotly==5.20.0 jupyter==1.0.0
```

#### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run     # validate config, print URLs, no network
python src/ingestion/ingest.py               # fetch -> data/raw/
python src/transformation/transform.py       # clean, features, ML -> data/processed/
python src/loading/load.py                   # -> data/climate_monitoring.db
```

#### 3. Explore the results

```bash
sqlite3 -header -column data/climate_monitoring.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

#### 4. Run with Docker

```bash
cd docker
docker compose up --build pipeline     # runs ingest -> transform -> load
docker compose up -d adminer           # http://localhost:8080
```

In Adminer select **SQLite 3**, database `/data/climate_monitoring.db`, empty username, password `adminer`.

#### 5. Orchestrate with Airflow

```bash
export AIRFLOW_HOME=~/airflow
export CLIMATE_PROJECT_ROOT=$(pwd)
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
airflow dags test climate_monitoring_pipeline 2026-11-11
```

The DAG runs Mon/Wed/Fri at 06:00 with `retries=2` and `retry_delay=5 minutes`; `generate_report` writes
`data/reports/pipeline_report_<date>.txt`.

#### 6. CI

`.github/workflows/ci.yml` runs on every push and pull request: `flake8` lint, then a config check and
`ingest.py --dry-run` on Python 3.11 (installs only pandas, requests, pyyaml and flake8 — no Airflow).

---

### Key Insights

Figures computed from the World Bank data in `data/raw/` by this pipeline.

1. **Africa is losing forest about 10× faster than the USA.** Average forest cover across the African sample fell
   from 29.1% (2015) to 27.7% (2024), losing around 0.5% of the remaining forest each year. US forest cover barely
   moved (33.9% → 33.8%, about 0.02-0.05% per year).
2. **The energy poverty paradox.** Renewable share and electricity access are strongly negatively correlated
   (Pearson r = −0.82 over 221 country-years). Liberia (92.8%), Uganda (90.9%) and Ethiopia (90.6%) top the
   renewable rankings mostly because they rely on biomass, while the USA (100% access) sits at 10.9%.
3. **East Africa leads electrification.** Between 2015 and 2024 access rose by 49.2 pp in Rwanda, 36.8 pp in Uganda
   and 35.4 pp in Kenya. Eight countries are still below 50% access, lowest Niger (21.3%) and Burkina Faso (34.0%).
4. **A huge consumption gap.** Electricity use averages about 13,064 kWh per person in the USA against about
   452 kWh across the African countries that report it.
5. **Diverging ML forecasts (2025-2027).** The linear models project the USA rising to about 12.6% renewables by 2027
   (R² 0.93), and South Africa (≈11.1%) and Egypt (≈7.0%) rising slowly from a fossil-heavy base. Biomass-dependent
   countries such as Côte d'Ivoire, Ghana, Mali and Guinea trend down by 1-1.6 pp per year as modern (often fossil)
   energy replaces firewood.
6. **Agricultural pressure on land.** Agricultural land covers more than 70% of land area in Côte d'Ivoire, Uganda,
   South Africa, Nigeria, Gambia, Rwanda, Guinea and Madagascar, which ties land-use change closely to forest decline.

> **Model caveat:** each forecast is a per-country linear trend fitted on 9-10 annual points (median in-sample
> R² ≈ 0.52). Treat it as a clear baseline to compare against, not a policy-grade projection.

---

### Technologies

| Technology | Purpose |
|---|---|
| **Python 3.11** | Pipeline language |
| **pandas / NumPy** | Cleaning, reshaping, feature engineering |
| **scikit-learn** | `LinearRegression` renewable-energy forecasting |
| **requests** | World Bank API client with retry/backoff |
| **SQLite** | Analytical warehouse (`data/climate_monitoring.db`) |
| **Apache Airflow** | Scheduled orchestration (Mon/Wed/Fri 06:00) |
| **Docker / Docker Compose** | Reproducible pipeline container + Adminer DB UI |
| **Plotly** | Interactive charts in the analysis notebook |
| **GitHub Actions** | CI: flake8 lint + ingestion dry-run smoke test |

### License & Attribution

Data: World Bank Open Data, licensed under [CC BY 4.0](https://datacatalog.worldbank.org/public-licenses).
