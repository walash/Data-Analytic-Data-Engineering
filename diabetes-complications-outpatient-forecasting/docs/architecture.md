# System Architecture

## 1. Overview

This project implements a layered, config-driven healthcare analytics platform.
Each layer is an independent, importable Python package under `src/`, orchestrated
either individually (`python -m src.<pkg>.<module>`) or through the `main.py` CLI.

```
                +-------------------------------------------------------------+
                |                    config/config.yaml                       |
                |   (single source of truth: paths, seeds, hyper-params)      |
                +-------------------------------------------------------------+
                                          |
        +---------------------------------+---------------------------------+
        |                 |                 |                 |             |
        v                 v                 v                 v             v
+---------------+  +---------------+  +---------------+  +---------------+  +-----------+
| DATA          |  | EDA           |  | MODELING      |  | FORECASTING   |  | UTILS     |
| ENGINEERING   |  |               |  |               |  |               |  |           |
+---------------+  +---------------+  +---------------+  +---------------+  +-----------+
| data_generator|  | descriptive   |  | feature_      |  | load_         |  | common:   |
| schema (pyd.) |  | statistical_  |  |   selector    |  |   forecaster  |  | config,   |
| etl_pipeline  |  |   tests       |  | model_trainer |  | resource_     |  | paths,    |
| feature_eng.  |  | visualizations|  | model_        |  |   scheduler   |  | logger,   |
| data_catalog  |  | eda_report    |  |   evaluator   |  | schedule_     |  | seed,     |
|               |  |               |  | model_registry|  |   optimizer   |  | constants |
|               |  |               |  | deterioration_|  | forecast_     |  |           |
|               |  |               |  |   predictor   |  |   dashboard   |  |           |
+-------+-------+  +-------+-------+  +-------+-------+  +-------+-------+  +-----------+
        |                  |                  |                  |
        v                  v                  v                  v
   data/raw/          outputs/figures/   models/*.pkl      outputs/reports/
   data/processed/    outputs/reports/   model_registry    load_forecast.csv
   data/feature_store/                   .json             resource_schedule.csv
   docs/data_catalog.json                                  forecast_dashboard.html
```

## 2. Data flow

1. **Data generation** — `data_generator.py` produces five synthetic, non-PHI raw
   CSVs (patients, labs, outpatient_visits, clinical_notes, complications) with a
   realistic latent-risk model linking demographics/labs to complications and to a
   12-month deterioration flag.
2. **Ingestion & quality** — `etl_pipeline.py` ingests raw files, runs data-quality
   checks (completeness, ranges, duplicates), then cleans and standardises records
   (type coercion, outlier capping, missing-value imputation) and extracts NLP
   keyword flags from clinical notes.
3. **Feature engineering** — `feature_engineering.py` joins the cleaned sources into
   a modelling-ready feature store (`features.parquet`, 12k patients x 43 columns)
   and builds a weekly outpatient demand series (`weekly_demand.parquet`, total +
   per clinic).
4. **Cataloguing** — `data_catalog.py` profiles every dataset and writes
   `docs/data_catalog.json`.
5. **Analytics layers** consume the feature store:
   - **EDA** — descriptive stats, hypothesis tests and 10 figures.
   - **Modeling** — feature selection, four classifiers, evaluation, calibration,
     a model registry and an inference interface for patient deterioration risk.
   - **Forecasting** — Prophet+SARIMA ensemble demand forecast, resource scheduling,
     PuLP cost optimisation and an HTML dashboard.

## 3. Design principles

- **Config-driven** — all tunables live in `config/config.yaml`; no magic numbers.
- **Reproducible** — a single global seed (42) governs data generation and modelling.
- **Path-safe** — every path is resolved through `src.utils.common.get_paths()`
  relative to the project root, so modules run identically from any working dir.
- **Schema-validated** — Pydantic v2 contracts guard the shape of each raw source.
- **Leakage-aware** — the raw complication label is excluded from the deterioration
  model's feature set; performance (ROC-AUC ~0.87) is credible rather than inflated.
- **Graceful degradation** — the forecaster falls back from ensemble to SARIMA-only
  to seasonal-naive; the optimiser falls back from PuLP to a closed-form solution.
