# Maternal & Child Health in Africa vs the USA — A Data Engineering Pipeline

**Domain:** Health · **Focus:** Maternal & child health · **Geography:** 15 African countries + United States · **Period:** 2013–2024 · **Built:** 2026-09-30

This project builds an end-to-end ETL pipeline on World Bank Open Data to compare maternal, newborn and child
health outcomes, and the factors that drive them, across 15 African countries and the United States. Raw
API data is ingested, cleaned and enriched with pandas, loaded into a SQLite star-style schema, analysed with SQL
and a Jupyter notebook, orchestrated with Airflow, containerised with Docker and checked by GitHub Actions CI.

---

## Problem statement

Sub-Saharan Africa accounts for roughly 70% of global maternal deaths. The UN Sustainable Development Goals set
2030 targets of **MMR < 70 per 100,000 live births (SDG 3.1)**, **under-5 mortality ≤ 25** and **neonatal
mortality ≤ 12 per 1,000 live births (SDG 3.2)**. This project answers:

1. How large is the Africa–USA gap in maternal and child mortality, and is it closing?
2. Which African countries are improving fastest, and which are stagnating?
3. How do fertility, skilled birth attendance, immunization and health spending relate to outcomes?
4. How did the COVID-19 period (2020–2021) affect maternal mortality?

**Countries:** Nigeria, Ghana, Kenya, Ethiopia, South Africa, Egypt, Morocco, Tanzania, Uganda, Senegal, Cameroon,
Côte d'Ivoire, Zambia, Rwanda, Mozambique, and the United States (the benchmark).

---

## Data sources

All data comes from the free, keyless **World Bank Open Data API** (`https://api.worldbank.org/v2/`), requesting the 10
most recent values per country (`mrv=10`).

| Indicator | Code | Unit | API URL |
|---|---|---|---|
| Maternal mortality ratio | `SH.STA.MMRT` | per 100,000 live births | https://api.worldbank.org/v2/country/NG;US/indicator/SH.STA.MMRT?format=json&mrv=10 |
| Under-5 mortality rate | `SH.DYN.MORT` | per 1,000 live births | https://api.worldbank.org/v2/country/NG;US/indicator/SH.DYN.MORT?format=json&mrv=10 |
| Neonatal mortality rate | `SH.DYN.NMRT` | per 1,000 live births | https://api.worldbank.org/v2/country/NG;US/indicator/SH.DYN.NMRT?format=json&mrv=10 |
| Births attended by skilled staff | `SH.STA.BRTC.ZS` | % of births | https://api.worldbank.org/v2/country/NG;US/indicator/SH.STA.BRTC.ZS?format=json&mrv=10 |
| Health expenditure per capita | `SH.XPD.CHEX.PC.CD` | current US$ | https://api.worldbank.org/v2/country/NG;US/indicator/SH.XPD.CHEX.PC.CD?format=json&mrv=10 |
| DPT immunization | `SH.IMM.IDPT` | % of children 12–23 months | https://api.worldbank.org/v2/country/NG;US/indicator/SH.IMM.IDPT?format=json&mrv=10 |
| Total fertility rate | `SP.DYN.TFRT.IN` | births per woman | https://api.worldbank.org/v2/country/NG;US/indicator/SP.DYN.TFRT.IN?format=json&mrv=10 |

Indicator documentation: https://data.worldbank.org/indicator/ (for example https://data.worldbank.org/indicator/SH.STA.MMRT).
The URLs above use two countries to stay short. The pipeline requests all 16 ISO-2 codes in a single call per indicator.

**Coverage of the downloaded data:** six indicators have complete panels (160 rows = 16 countries × 10 years).
Skilled birth attendance comes from household surveys, so it has only 44 rows (2013–2022).

---

## Architecture

```
                 +---------------------------------------------+
                 |     World Bank Open Data API (REST/JSON)    |
                 |  api.worldbank.org/v2/country/{16}/indicator |
                 |  MMRT | MORT | NMRT | BRTC | XPD | IDPT | TFRT|
                 +----------------------+----------------------+
                                        | HTTPS, paginated, retries (tenacity)
                                        v
+----------------------------------------------------------------------------+
| 1. INGEST   src/ingestion/ingest.py                                        |
|    - builds URLs from config/config.yaml   - exponential backoff on 429/5xx|
|    - --dry-run validation mode             - tidy CSV per indicator + JSON |
+-------------------------------------+--------------------------------------+
                                      v
                     data/raw/*.csv  +  data/raw/all_indicators.json
                                      |
                                      v
+----------------------------------------------------------------------------+
| 2. TRANSFORM   src/transformation/transform.py                             |
|    - schema check, type casting        - dedupe, range validation          |
|    - canonical names, region tagging   - pivot long -> wide (country-year) |
|    - interior-gap interpolation        - derived metrics + SDG flags       |
|    - regional yearly aggregates                                            |
+-------------------------------------+--------------------------------------+
                                      v
      data/processed/  maternal_child_health_long.csv | maternal_child_health_wide.csv
                       country_metadata.csv           | yearly_summary.csv
                                      |
                                      v
+----------------------------------------------------------------------------+
| 3. LOAD   src/loading/load.py  ->  data/maternal_child_health.db (SQLite)  |
|    country_metadata (dim) 1---* maternal_child_health_metrics (fact)       |
|    yearly_summary (region x year aggregate)  | PK/FK, CHECKs, indexes      |
+------------------+--------------------------+------------------------------+
                   |                          |
                   v                          v
     +---------------------------+   +-------------------------------+
     | 4a. sql/queries.sql       |   | 4b. notebooks/analysis.ipynb  |
     |  8 analytical queries     |   |  EDA, 8 visualizations        |
     |  (CTEs, window functions) |   |  matplotlib/seaborn/plotly    |
     +---------------------------+   +-------------------------------+

  Orchestration: dags/pipeline_dag.py (Airflow, @weekly, retries=2)
     ingest_data >> transform_data >> load_to_db >> generate_report (data/reports/*.md)
  Packaging: docker/Dockerfile + docker/docker-compose.yml (SQLite named volume)
  Quality:   .github/workflows/ci.yml (flake8 + ingest --dry-run + transform/load smoke test)
```

### Database schema

| Table | Grain | Key columns |
|---|---|---|
| `country_metadata` | country | `country_code` (PK, ISO-3), `iso2_code`, `country_name`, `region` (Africa/USA), `subregion`, `is_africa` |
| `maternal_child_health_metrics` | country × year | 7 indicators, `imputed_values_count`, `neonatal_share_of_under5_pct`, `post_neonatal_under5_rate`, `meets_sdg_mmr/u5mr/nmr`, `mmr_yoy_change_pct`, `u5mr_yoy_change_pct` |
| `yearly_summary` | region × year | `avg_*` for each indicator, `countries_reporting_mmr`, `median_maternal_mortality_ratio` |

---

## Key insights

These figures come from the processed data and the queries in `sql/queries.sql`.

1. **A large gap that is slowly closing.** The unweighted African average MMR fell from **347 (2014) to 233 (2023)** per
   100,000 live births, but it is still **~14× the US level (17)**. Average African under-5 mortality (48.6 in 2023) is
   **~7.5× the US rate (6.5)**, down from 8.7× in 2015.
2. **Nigeria is the outlier.** Its MMR of **993** (2023) is 58× the US rate and 2.8× the next-highest country in the
   sample (Côte d'Ivoire, 359). Nigeria cut MMR by only 14% in a decade, and its under-5 mortality (115.6) barely moved.
3. **Some countries improved fast.** Ethiopia (−56%), Mozambique (−53%) and Zambia (−51%) more than halved MMR
   between 2014 and 2023. That is an annual rate of reduction of 7.7–8.7%, above the ~6.4% pace that SDG 3.1 requires.
4. **SDG 3.1 status.** Egypt is the only African country in the sample below 70 (MMR 17, on par with the USA). Morocco
   sits exactly at the threshold (70).
5. **Immunization tracks child survival.** African country-years with DPT coverage ≥ 90% average **39.3** under-5 deaths per
   1,000 births. Those below 70% average **87.6**.
6. **Drivers correlate strongly.** In African data, neonatal vs under-5 mortality r = 0.93, fertility vs under-5
   mortality r = 0.72, skilled birth attendance vs MMR r = −0.62, and health spending vs neonatal mortality r = −0.58.
7. **Spending is not the same as outcomes.** The USA spends about **US$13,473 per person**, around 120× the African
   average (~US$112). Yet its MMR is high for a rich country. Rwanda and Tanzania spend under US$50 and still have lower
   child mortality than higher-spending Nigeria, Cameroon and Côte d'Ivoire.
8. **COVID-19 reversal.** 15 of 16 countries had an MMR increase in 2020 or 2021. The US MMR rose from 18 to **31**
   (2019 → 2021), and South Africa's jumped 62% in 2021. Most series recovered by 2023.

*Caveats:* MMR, U5MR and NMR are modeled estimates with uncertainty intervals. Skilled-attendance data is sparse
and survey-based. Regional averages are unweighted by population.

---

## How to run

### 1. Set up the environment
```bash
cd 2026-09-30_health_africa_usa_maternal_child_health
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
`apache-airflow` is only needed for the DAG. For the core pipeline alone, run
`pip install requests==2.31.0 tenacity==8.2.3 pyyaml==6.0.1 pandas==2.1.4 numpy==1.26.3`.

### 2. Run the pipeline stage by stage
```bash
python src/ingestion/ingest.py --dry-run          # validate config and print API URLs (no network)
python src/ingestion/ingest.py                    # fetch all 7 indicators -> data/raw/
python src/ingestion/ingest.py --indicators maternal_mortality_ratio fertility_rate   # subset
python src/transformation/transform.py            # -> data/processed/*.csv
python src/loading/load.py                        # -> data/maternal_child_health.db
```
Paths can be overridden with the environment variables `CONFIG_PATH`, `RAW_DATA_DIR`, `PROCESSED_DATA_DIR`, `DB_PATH`
and `LOG_LEVEL`.

### 3. Explore the results
```bash
sqlite3 data/maternal_child_health.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```
The notebook reads the raw CSVs in `data/raw/` directly, so it only needs the ingestion step to have run.

### 4. Run with Docker
```bash
cd docker
docker compose up --build                                          # ingest -> transform -> load
docker compose run --rm pipeline python src/ingestion/ingest.py --dry-run
docker compose --profile tools run --rm sqlite-cli                 # run sql/queries.sql against the DB volume
```
Built by itself, the image's default `CMD` runs only the ingestion step:
`docker build -f docker/Dockerfile -t mch-pipeline . && docker run --rm -v "$PWD/data/raw:/app/data/raw" mch-pipeline`.

### 5. Orchestrate with Airflow
```bash
export AIRFLOW_HOME=~/airflow
export MCH_PROJECT_ROOT="$PWD"
airflow db migrate
cp dags/pipeline_dag.py "$AIRFLOW_HOME/dags/"
airflow dags test maternal_child_health_africa_usa_pipeline 2026-09-30
```
The DAG runs `@weekly` with `retries=2`. Its last task writes `data/reports/pipeline_report_<date>.md`.

### 6. CI
`.github/workflows/ci.yml` runs on every push and pull request with Python 3.11. It lints `src/` and `dags/` with
flake8, runs `ingest.py --dry-run`, and runs transform and load on the committed raw data.

---

## Technologies

Python 3.11 · requests + tenacity (resilient HTTP) · pandas / NumPy · SQLite · SQL (CTEs, window functions) ·
Jupyter, matplotlib, seaborn, Plotly · Apache Airflow 2.8 · Docker / Docker Compose · GitHub Actions · YAML config

---

## Project structure

```
2026-09-30_health_africa_usa_maternal_child_health/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml                 # API endpoints, countries, indicators, paths, pipeline params
├── data/
│   ├── raw/                        # 7 indicator CSVs + all_indicators.json (World Bank API)
│   ├── processed/                  # long, wide, country_metadata, yearly_summary CSVs
│   └── maternal_child_health.db    # SQLite database (created by load.py)
├── src/
│   ├── ingestion/ingest.py         # World Bank API -> data/raw
│   ├── transformation/transform.py # clean, pivot, enrich -> data/processed
│   └── loading/load.py             # schema + load -> SQLite
├── sql/
│   └── queries.sql                 # 8 analytical queries
├── notebooks/
│   └── analysis.ipynb              # EDA with 8 visualizations
├── dags/
│   └── pipeline_dag.py             # Airflow DAG (@weekly)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── .github/
    └── workflows/ci.yml            # flake8 + dry-run smoke test
```

---

## License and attribution

Data is © The World Bank and licensed under
[CC BY 4.0](https://datacatalog.worldbank.org/public-licenses#cc-by). Please cite "World Bank, World Development
Indicators" when you reuse it.
