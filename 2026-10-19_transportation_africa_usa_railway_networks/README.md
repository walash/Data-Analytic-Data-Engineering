# Africa & USA Railway Networks — Cross-Border Rail Infrastructure & Trade Analysis

**Domain:** Global Transportation
**Sub-theme:** Railway networks: cross-border rail infrastructure, freight and passenger traffic
**Geographic focus:** All 54 African countries, grouped into five sub-regions, compared against the **United States**
**Pipeline:** World Bank API → Python ETL → SQLite warehouse → SQL analytics and Jupyter visualisations, scheduled with Airflow, packaged with Docker, checked by GitHub Actions

---

## Problem Statement

Rail is the cheapest way to move bulk goods over land, and many of Africa's trade corridors were built around
it. Examples include the Douala–N'Djamena corridor serving landlocked Chad, the Abidjan–Ouagadougou line linking Côte
d'Ivoire and Burkina Faso, the DRC's Katanga mineral lines and Algeria's coastal network. Most of these
networks are short, old and lightly used. The USA, by contrast, runs one of the densest freight rail systems in the world.

This project builds a reproducible pipeline that answers four questions:

1. **Network size:** which African countries run the largest rail networks, and are they expanding or shrinking?
2. **Network use:** how much freight and passenger traffic moves per kilometre of track (freight and passenger density)?
3. **Infrastructure quality:** does a larger rail network go with a higher LPI infrastructure score?
4. **The gap with the USA:** how far African rail systems sit from the US benchmark on size, traffic and logistics quality.

## Data Sources

All data comes from the free, public **World Bank Indicators API v2**. No API key is needed.

| Indicator | Code | URL |
|---|---|---|
| Rail lines (total route-km) | `IS.RRS.TOTL.KM` | https://api.worldbank.org/v2/country/all/indicator/IS.RRS.TOTL.KM |
| Railways, goods transported (million ton-km) | `IS.RRS.GOOD.MT.K6` | https://api.worldbank.org/v2/country/all/indicator/IS.RRS.GOOD.MT.K6 |
| Railways, passengers carried (million passenger-km) | `IS.RRS.PASG.KM` | https://api.worldbank.org/v2/country/all/indicator/IS.RRS.PASG.KM |
| GDP per capita (current US$) | `NY.GDP.PCAP.CD` | https://api.worldbank.org/v2/country/all/indicator/NY.GDP.PCAP.CD |
| LPI: quality of trade & transport infrastructure (1–5) | `LP.LPI.INFR.XQ` | https://api.worldbank.org/v2/country/all/indicator/LP.LPI.INFR.XQ |

The ingestion script calls `/v2/country/{ISO2;ISO2;...}/indicator/{code}?format=json&date=2000:2025`
for the African and US country codes listed in `config/config.yaml`, in batches of 20 countries.

### Committed raw extract (`data/raw/`)

| File | Rows | Coverage |
|---|---|---|
| `wb_rail_lines_km.csv` | 51 | 7 countries (DZ, BW, BF, CM, CD, CG, CI), 2012–2021 |
| `wb_rail_freight_million_tkm.csv` | 22 | Algeria, Cameroon, DR Congo, 2012–2021 |
| `wb_rail_passengers_million_km.csv` | 23 | Algeria, Cameroon, DR Congo, Congo Rep., 2012–2021 |
| `wb_gdp_per_capita_usd.csv` | 175 | 35 African countries, 2021–2025 |
| `wb_lpi_infrastructure_score.csv` | 152 | 35 African countries, LPI editions 2012–2022 |

### Data notes

- The committed extract covers African countries from **Algeria to Chad** in alphabetical order. The
  World Bank API returned HTTP 403 for later country batches from the build environment, so the **USA
  benchmark and the rest of Africa (e.g. South Africa, Kenya, Nigeria, Tanzania) are not in the snapshot yet.**
  Run `python src/ingestion/ingest.py` from an unrestricted network to fill them in. Every downstream stage,
  query and chart picks up the extra countries, including `US`, automatically.
- The World Bank files the **2023 LPI edition under year 2022**.
- Rail series are reported irregularly, so the transformation stage forward-fills route-km and LPI scores by
  up to 4 years (`*_filled` columns) before computing density ratios.
- Namibia's ISO2 code `NA` is read as text, not as a missing value, at every stage.

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │               World Bank Indicators API v2  (public JSON, no key)        │
 │  IS.RRS.TOTL.KM · IS.RRS.GOOD.MT.K6 · IS.RRS.PASG.KM ·                   │
 │  NY.GDP.PCAP.CD · LP.LPI.INFR.XQ        (54 African countries + US)      │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     │ HTTPS · 20-country batches · paging
                                     │ 3 retries, exponential backoff
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1. INGEST     src/ingestion/ingest.py          (--dry-run supported)     │
 │    → data/raw/wb_<indicator>.csv  (country_code, country_name, year, v)  │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2. TRANSFORM  src/transformation/transform.py                            │
 │    trim/cast · drop nulls & non-positive · dedup (country, year) ·       │
 │    outer-merge 5 series · region/sub-region tags · forward-fill ·        │
 │    freight & passenger density · YoY growth                              │
 │    → data/processed/railway_master.csv                                   │
 │    → data/processed/country_profiles.csv                                 │
 └───────────────────────────────────┬──────────────────────────────────────┘
                                     ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3. LOAD       src/loading/load.py  →  data/railway_networks.db (SQLite)  │
 │    country_profiles ─┬─< rail_infrastructure                             │
 │                      ├─< rail_freight                                    │
 │                      └─< rail_passengers        + view v_rail_master     │
 └──────────────┬──────────────────────────────────────┬────────────────────┘
                ▼                                      ▼
 ┌──────────────────────────────┐      ┌───────────────────────────────────┐
 │ 4a. sql/queries.sql          │      │ 4b. notebooks/analysis.ipynb      │
 │     8 analytical queries     │      │     6 visualisations + narrative  │
 └──────────────────────────────┘      └───────────────────────────────────┘

 Orchestration : dags/pipeline_dag.py  (Airflow, @weekly, retries=2)
                 ingest_data → transform_data → load_to_db → generate_report
 Packaging     : docker/Dockerfile + docker/docker-compose.yml
 CI            : .github/workflows/ci.yml  (flake8 + ingestion --dry-run)
```

### Warehouse schema

| Table | Grain | Key columns |
|---|---|---|
| `country_profiles` | country | region, subregion, is_africa, latest rail km / freight / passengers / GDP / LPI with their years |
| `rail_infrastructure` | country-year | rail_lines_km, rail_lines_km_filled, rail_lines_yoy_pct, gdp_per_capita_usd, lpi_infrastructure_score |
| `rail_freight` | country-year | rail_freight_million_tkm, freight_density_mtkm_per_km, rail_freight_yoy_pct |
| `rail_passengers` | country-year | rail_passengers_million_km, passenger_density_mpkm_per_km, freight_share_of_traffic_pct |

## Key Insights (from the committed extract)

1. **Two networks make up most of the reported track.** Algeria (4,000.5 km, 2021) and DR Congo (3,641 km)
   together account for about 67% of the 11,462 route-km reported by the seven African countries with rail data.
   Every other reporting network is under 1,000 km.
2. **Cameroon has the busiest track.** Cameroon moves about 1.01 million ton-km of freight per route-km on
   average (2017–2021), four times Algeria's 0.25 and more than twenty times the DRC's 0.047. Much of the
   traffic is Douala port freight bound for Chad and the Central African Republic.
3. **Long networks are not well-rated networks.** Across the seven countries, the correlation between
   route-km and LPI infrastructure score is **−0.37**. Botswana (3.1) and Côte d'Ivoire (2.89) score highest
   with short networks. Algeria (2.1) and the DRC (2.3) score lower despite having the longest networks.
4. **Some networks are shrinking.** Cameroon's reported network fell from 1,004 km (2016) to 884 km (−12%),
   and Algeria's from 4,175 km (2012) to 4,000.5 km (−4%). The other networks have stayed the same size for a decade.
5. **COVID-19 hit passenger rail hard.** Passenger-km fell 76% in Algeria and 80% in the DRC in 2020.
   Before that, Algeria grew steadily from 1,141 to a peak of 1,602 million passenger-km in 2018.
6. **Data gaps are a finding too.** Only 7 of 35 African countries in the extract report rail route-km, and
   only 3 report freight. East Africa reports no rail lines at all in this extract.
7. **The USA comparison.** African networks are tiny next to the US freight rail system, which the
   Association of American Railroads puts at roughly 140,000 route-miles (about 225,000 km). Even the
   largest African networks are less than 2% of that length. Run Q6 in `sql/queries.sql` and section 6 of
   the notebook after a full ingestion that includes `US` for a like-for-like comparison using World Bank data.

## How to Run

### 1. Set up the environment

```bash
cd 2026-10-19_transportation_africa_usa_railway_networks
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
python src/ingestion/ingest.py --dry-run          # validate config, print request plan
python src/ingestion/ingest.py                    # fetch all 5 indicators → data/raw/
python src/transformation/transform.py            # → data/processed/railway_master.csv
python src/loading/load.py                        # → data/railway_networks.db
```

Useful options: `--indicator rail_lines_km` (repeatable) and `--output-dir <dir>` on the ingestion script,
`--config <path>` on every stage, and the `CONFIG_PATH`, `RAILWAY_DB_PATH` and `LOG_LEVEL` environment variables.

### 3. Explore the results

```bash
sqlite3 -header -column data/railway_networks.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build        # runs ingest → transform → load; outputs persist in ../data
```

### 5. Schedule with Airflow

```bash
export AIRFLOW_HOME=~/airflow
airflow db migrate
cp dags/pipeline_dag.py $AIRFLOW_HOME/dags/
export RAILWAY_PROJECT_ROOT=$(pwd)
airflow dags test africa_usa_railway_networks_pipeline 2026-10-19
```

The `generate_report` task writes `reports/pipeline_report.md` with table row counts, the largest African
networks and regional totals.

## SQL Analyses (`sql/queries.sql`)

| # | Query |
|---|---|
| Q1 | Top African countries by rail network size, with route-km per US$1k of GDP per capita |
| Q2 | Freight efficiency: freight ton-km per route-km |
| Q3 | Passenger trends with year-on-year change (window functions) |
| Q4 | Correlation between LPI infrastructure score and rail network size (Pearson r in SQL) |
| Q5 | Year-over-year growth across rail lines, freight and passengers |
| Q6 | USA vs. Africa comparison |
| Q7 | Countries whose rail networks are shrinking |
| Q8 | Regional groupings: rail coverage by African sub-region |

## Technologies

Python 3.11 · pandas · NumPy · requests · PyYAML · SQLite · matplotlib · seaborn · Plotly · Jupyter ·
Apache Airflow 2.8 · Docker / Docker Compose · GitHub Actions · flake8

## Project Structure

```
2026-10-19_transportation_africa_usa_railway_networks/
├── .github/workflows/ci.yml        # lint + ingestion dry-run smoke test
├── config/config.yaml              # API endpoints, regions, paths, pipeline & DB settings
├── dags/pipeline_dag.py            # Airflow DAG (@weekly)
├── data/
│   ├── raw/                        # World Bank CSV extracts (committed)
│   ├── processed/                  # railway_master.csv, country_profiles.csv (generated)
│   └── railway_networks.db         # SQLite warehouse (generated)
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA: 6 visualisations + insights
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```
