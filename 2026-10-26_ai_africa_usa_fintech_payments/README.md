# AI-Driven Fintech & Digital Payments: Africa and USA

| | |
|---|---|
| **Domain** | AI / Fintech |
| **Geographic focus** | 15 African countries (Nigeria, Kenya, Ghana, South Africa, Ethiopia, Tanzania, Uganda, Senegal, Côte d'Ivoire, Cameroon, Egypt, Morocco, Tunisia, Rwanda, Zambia) + the United States |
| **Data** | World Bank Indicators API (public, no API key required) |
| **Pipeline** | Python ETL → SQLite, orchestrated with Apache Airflow, containerised with Docker |

## Problem statement

AI-driven credit scoring, fraud detection, chatbot banking and mobile-money platforms are changing how people in Africa save,
borrow and move money. None of it works without three foundations: **connectivity** (internet and mobile networks),
**accounts** (adults who can transact digitally) and **payment flows** (remittances are the largest cross-border payment
stream into many African economies, and the United States is a major sending country).

This project builds an end-to-end data pipeline to answer:

1. Which African countries have the highest financial inclusion, and how fast has it improved since 2017?
2. Does mobile and internet penetration actually go hand in hand with financial inclusion?
3. How large are remittance inflows, and which economies depend on them most (as a % of GDP)?
4. How "fintech-ready" is each country compared with the U.S. benchmark?

## Data sources

All series come from the World Bank Indicators API for
`NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US` (most recent 5 values per country):

| Dataset | Indicator | URL |
|---|---|---|
| Financial inclusion (account ownership, % adults 15+) | `FX.OWN.TOTL.ZS` | https://api.worldbank.org/v2/country/NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US/indicator/FX.OWN.TOTL.ZS?format=json&per_page=500&mrv=5 |
| Internet users (% of population) | `IT.NET.USER.ZS` | https://api.worldbank.org/v2/country/NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US/indicator/IT.NET.USER.ZS?format=json&per_page=500&mrv=5 |
| Mobile subscriptions (per 100 people) | `IT.CEL.SETS.P2` | https://api.worldbank.org/v2/country/NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US/indicator/IT.CEL.SETS.P2?format=json&per_page=500&mrv=5 |
| Personal remittances received (current US$) | `BX.TRF.PWKR.CD.DT` | https://api.worldbank.org/v2/country/NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US/indicator/BX.TRF.PWKR.CD.DT?format=json&per_page=500&mrv=5 |
| GDP per capita (current US$) | `NY.GDP.PCAP.CD` | https://api.worldbank.org/v2/country/NG;KE;GH;ZA;ET;TZ;UG;SN;CI;CM;EG;MA;TN;RW;ZM;US/indicator/NY.GDP.PCAP.CD?format=json&per_page=500&mrv=5 |

The pipeline also pulls population (`SP.POP.TOTL`) from the same API. This lets it convert GDP per capita into total GDP
for the remittance-to-GDP ratio.

## Architecture

```
 +----------------------------+
 |  World Bank Indicators API |   FX.OWN.TOTL.ZS, IT.NET.USER.ZS, IT.CEL.SETS.P2,
 |  (public REST / JSON)      |   BX.TRF.PWKR.CD.DT, NY.GDP.PCAP.CD (+ SP.POP.TOTL)
 +-------------+--------------+
               |
               v
 +----------------------------+     src/ingestion/ingest.py
 |  1. INGESTION              |     requests, pagination, 3 retries with
 |  -> data/raw/*.csv         |     exponential backoff, atomic writes, --dry-run
 +-------------+--------------+
               |
               v
 +----------------------------+     src/transformation/transform.py
 |  2. TRANSFORMATION         |     clean nulls / types / duplicates, merge on
 |  -> data/processed/        |     country+year, fintech_readiness_score,
 |     fintech_master.csv     |     remittance_to_gdp_ratio, YoY growth
 |     country_summary.csv    |
 +-------------+--------------+
               |
               v
 +----------------------------+     src/loading/load.py
 |  3. LOADING                |     schema DDL, INSERT ... ON CONFLICT upserts,
 |                            |     row-count verification
 +-------------+--------------+
               |
               v
 +----------------------------+
 |  SQLite DB                 |     raw_indicators | fintech_master | country_summary
 |  data/fintech_africa_usa.db|
 +------+--------------+------+
        |              |
        v              v
 +-------------+  +---------------------------+
 | sql/        |  | notebooks/analysis.ipynb  |   matplotlib, seaborn, plotly
 | queries.sql |  | (EDA + visualisations)    |
 +-------------+  +---------------------------+

 Orchestration: dags/pipeline_dag.py (Airflow, Mon/Wed/Fri 06:00)
   ingest_data >> transform_data >> load_data >> generate_report
 Packaging: docker/Dockerfile + docker/docker-compose.yml (pipeline + sqlite-web viewer)
 CI: .github/workflows/ci.yml (flake8 + ingest --dry-run smoke test)
```

### Derived metrics

- **`fintech_readiness_score`** (0-100) = 0.4 × account ownership + 0.3 × internet users + 0.3 × mobile subscriptions
  (capped at 100 per 100 people). Findex survey values are carried forward between survey rounds.
- **`remittance_to_gdp_ratio`** (%) = remittances received ÷ (GDP per capita × population) × 100.

## How to run

```bash
cd 2026-10-26_ai_africa_usa_fintech_payments
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Ingest (use --dry-run to validate config and print the API URLs without downloading)
python src/ingestion/ingest.py
# 2. Transform
python src/transformation/transform.py
# 3. Load into SQLite
python src/loading/load.py

# Explore
sqlite3 -header -column data/fintech_africa_usa.db < sql/queries.sql
jupyter notebook notebooks/analysis.ipynb
```

**Docker** (from the `docker/` directory):

```bash
docker compose up --build
# pipeline runs ingest -> transform -> load, then the sqlite-web viewer starts on http://localhost:8080
```

**Airflow:** Airflow 2.8.0 needs `sqlalchemy<2.0`, so install it in a separate environment using the official constraints file
(see `requirements.txt`). Then copy or symlink `dags/pipeline_dag.py` into your `$AIRFLOW_HOME/dags` folder and enable
`fintech_africa_usa_pipeline`. Each run writes `reports/pipeline_report_<date>.md`.

## Key insights

Figures come from the latest World Bank values (Findex 2024 for inclusion; 2024/2025 for the other series):

- **Mobile money, not connectivity, drives inclusion in sub-Saharan Africa.** Kenya has 90% account ownership with only about 35%
  internet use, and Uganda has 73% with about 9%. Tunisia, Egypt and Morocco have 74-91% internet use but under 45% account
  ownership. Across the 16 countries, the correlation between inclusion and internet use is about 0.03, and with mobile
  subscriptions about 0.14.
- **High mobile penetration is necessary but not sufficient.** Côte d'Ivoire (184 subscriptions per 100 people) and Morocco (153)
  have high mobile penetration but below-median inclusion. These countries have the most room for mobile-money and e-KYC growth.
- **Inclusion is rising fast.** Between 2017 and 2024, account ownership rose by 34 pp in Senegal and by 23-27 pp in Zambia,
  Cameroon, Nigeria and Ghana, compared with +4 pp in the United States (93% → 97%).
- **Remittances are a major cross-border payment stream.** The 15 African countries received about US$108bn in their latest
  reported year. Egypt (about US$41.5bn) and Nigeria (about US$22.8bn) lead. Remittances exceed 5% of GDP in Egypt (11.4%),
  Senegal (10.6%), Nigeria (7.8%), Morocco (7.6%) and Tunisia (6.7%), which makes the U.S.-Africa corridor a prime target for
  AI-based FX pricing, compliance automation and fraud detection.
- **The readiness gap is large.** The United States scores 97 on the composite readiness score. South Africa (86) and Ghana (84)
  lead Africa, while Ethiopia (46), Uganda (57) and Rwanda (57) trail mainly because of low internet penetration.

*Caveats:* Findex data is survey-based and periodic (Rwanda's latest round is 2017), and with 16 countries the correlations are
descriptive, not causal.

## Technologies

Python 3.11 · pandas · NumPy · requests · SQLite · Apache Airflow · Docker / Docker Compose · Jupyter ·
matplotlib · seaborn · plotly · PyYAML · GitHub Actions · flake8

## Project structure

```
2026-10-26_ai_africa_usa_fintech_payments/
├── .github/workflows/ci.yml        # flake8 lint + ingest --dry-run smoke test
├── config/config.yaml              # API, indicators, countries, paths, pipeline settings
├── dags/pipeline_dag.py            # Airflow DAG: fintech_africa_usa_pipeline
├── data/
│   ├── raw/                        # World Bank CSV extracts
│   ├── processed/                  # fintech_master.csv, country_summary.csv
│   └── fintech_africa_usa.db       # SQLite database
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── notebooks/analysis.ipynb        # EDA & visualisations
├── sql/queries.sql                 # 8 analytical queries
├── src/
│   ├── ingestion/ingest.py
│   ├── transformation/transform.py
│   └── loading/load.py
├── requirements.txt
└── README.md
```
