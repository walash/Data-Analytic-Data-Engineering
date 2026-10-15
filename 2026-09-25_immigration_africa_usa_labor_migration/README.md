# Africa-to-USA Labor Migration & Remittances Data Engineering Pipeline

**Domain:** Global Immigration
**Geographic Focus:** 15 African countries + the United States of America

An end-to-end, reproducible data engineering pipeline that ingests real labor-migration
and economic indicators from the **World Bank Indicators API**, transforms them into a
single analytical dataset, loads them into a SQLite warehouse, and surfaces insights
through SQL and a Jupyter notebook.

---

## Problem Statement

Labor migration from Africa to the United States is driven by a mix of economic push
factors (unemployment, low GDP per capita, net emigration of working-age people) and is
mirrored by **remittance flows** sent home by the diaspora. The United States is the
world's single largest source of remittances, making the USA→Africa corridor critical to
household incomes and macroeconomic stability across the continent.

This project answers questions such as:

- Which African countries receive the most remittances, and how dependent are their
  economies on those flows (as a % of GDP)?
- Where is emigration pressure highest (negative net migration combined with high
  unemployment)?
- How do GDP per capita and labor-market conditions relate to migration and remittance
  behavior?
- How have remittances and net migration trended over the most recent years?

**Countries in scope:** Nigeria (NG), Ghana (GH), Kenya (KE), Ethiopia (ET),
South Africa (ZA), Egypt (EG), Morocco (MA), Tanzania (TZ), Uganda (UG), Senegal (SN),
Cameroon (CM), Côte d'Ivoire (CI), Sierra Leone (SL), Liberia (LR), Gambia (GM).

---

## Data Sources (Real — World Bank API)

All data is fetched live from the public [World Bank Indicators API](https://data.worldbank.org/)
(no API key required):

| Indicator | Code | Endpoint |
|-----------|------|----------|
| Net migration | `SM.POP.NETM` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/SM.POP.NETM?format=json&per_page=500&mrv=10` |
| Remittances received (current US$) | `BX.TRF.PWKR.CD.DT` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/BX.TRF.PWKR.CD.DT?format=json&per_page=500&mrv=10` |
| Remittances (% of GDP) | `BX.TRF.PWKR.DT.GD.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/BX.TRF.PWKR.DT.GD.ZS?format=json&per_page=500&mrv=10` |
| GDP per capita (current US$) | `NY.GDP.PCAP.CD` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/NY.GDP.PCAP.CD?format=json&per_page=500&mrv=10` |
| Unemployment (% of labor force) | `SL.UEM.TOTL.ZS` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/SL.UEM.TOTL.ZS?format=json&per_page=500&mrv=10` |
| Population, total | `SP.POP.TOTL` | `https://api.worldbank.org/v2/country/NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;SL;LR;GM/indicator/SP.POP.TOTL?format=json&per_page=500&mrv=5` |

---

## Architecture

```
        +---------------------------+
        |   World Bank API (REST)   |
        |  6 indicators, 15 nations |
        +-------------+-------------+
                      |  HTTP GET (retry + backoff)
                      v
+---------------------------------------------------+
|                RAW DATA (data/raw/)               |
|  net_migration.csv   remittances_usd.csv          |
|  remittances_pct_gdp.csv  gdp_per_capita.csv      |
|  unemployment.csv    population.csv               |
+-------------------------+-------------------------+
                          |  src/ingestion/ingest.py
                          v
+---------------------------------------------------+
|                    INGESTION                       |
|  Fetch -> validate -> persist CSV                 |
+-------------------------+-------------------------+
                          |  src/transformation/transform.py
                          v
+---------------------------------------------------+
|                 TRANSFORMATION                     |
|  clean nulls -> cast types -> dedupe ->           |
|  merge -> derive metrics                          |
|  -> data/processed/master_labor_migration.csv    |
+-------------------------+-------------------------+
                          |  src/loading/load.py
                          v
+---------------------------------------------------+
|                     LOADING                        |
|  SQLite: countries, net_migration, remittances,   |
|  economic_indicators, master_analytics            |
|  -> data/labor_migration.db                       |
+-------------------------+-------------------------+
                          |
                          v
+---------------------------------------------------+
|                    ANALYTICS                       |
|  sql/queries.sql  +  notebooks/analysis.ipynb    |
|  (bar, line, scatter, heatmap, pie charts)        |
+---------------------------------------------------+

Orchestration: dags/pipeline_dag.py (Apache Airflow)
Packaging:     docker/Dockerfile + docker-compose.yml
CI:            .github/workflows/ci.yml (flake8 + smoke test)
```

---

## Project Structure

```
2026-09-25_immigration_africa_usa_labor_migration/
├── README.md
├── requirements.txt
├── config/
│   └── config.yaml
├── data/
│   ├── raw/                      # downloaded World Bank CSVs
│   └── processed/               # master_labor_migration.csv
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

---

## How to Run

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the pipeline stage by stage

```bash
# Ingest real data from the World Bank API into data/raw/
python src/ingestion/ingest.py

# Transform & merge into data/processed/master_labor_migration.csv
python src/transformation/transform.py

# Load into the SQLite warehouse data/labor_migration.db
python src/loading/load.py
```

Validate the ingestion configuration without downloading (used by CI):

```bash
python src/ingestion/ingest.py --dry-run
```

### 3. Explore the results

```bash
# Run the analytical SQL queries
sqlite3 data/labor_migration.db < sql/queries.sql

# Open the notebook
jupyter notebook notebooks/analysis.ipynb
```

### 4. Run with Docker

```bash
cd docker
docker compose up --build
```

### 5. Orchestrate with Airflow

Copy `dags/pipeline_dag.py` into your Airflow `dags/` folder. The DAG
`labor_migration_pipeline` runs `ingest → transform → load → generate_report`
on the schedule `0 6 * * 1,3,5` (Mon/Wed/Fri at 06:00).

---

## Key Insights

Derived from the real World Bank data loaded by this pipeline (latest available years):

- **Remittance flows are highly concentrated.** Egypt (~$41.5B), Nigeria (~$22.8B) and
  Morocco (~$13.7B) receive the largest absolute remittance inflows among the 15
  countries. As the world's largest remittance-sending country, the **USA** is a major
  source of these flows through the African diaspora.
- **Remittances are an economic lifeline.** They reach double-digit percentages of GDP
  for the most dependent economies (e.g. Egypt ~11% of GDP), cushioning households and
  supplying scarce foreign exchange.
- **Persistent net emigration.** Countries such as Uganda, Egypt, Kenya and Ghana post
  negative net migration in the latest year, reflecting sustained outflows of
  working-age population.
- **Economic push factors are visible.** Higher unemployment and lower GDP per capita
  align with emigration pressure and greater remittance dependence.
- **Policy implication.** Because remittances are a stabilizing, counter-cyclical income
  stream, lowering transfer costs on the USA→Africa corridor would directly increase the
  value reaching recipient households.

---

## Technologies

- **Python** — pipeline implementation
- **Pandas / NumPy** — data cleaning, merging and derived metrics
- **Requests** — World Bank API ingestion with retry + exponential backoff
- **SQLite / SQLAlchemy** — analytical warehouse
- **Apache Airflow** — scheduling and orchestration
- **Docker / Docker Compose** — containerized, reproducible runs
- **Jupyter / Matplotlib / Seaborn / Plotly** — analysis and visualization
- **GitHub Actions / flake8** — CI linting and smoke testing
