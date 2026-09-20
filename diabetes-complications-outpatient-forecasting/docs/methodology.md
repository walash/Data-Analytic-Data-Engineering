# Methodology

## 1. Synthetic data model

The dataset is fully synthetic and contains no protected health information. A
latent cardiometabolic-risk score `z` is computed per patient from standardised
clinical drivers (age, HbA1c, years since diagnosis, eGFR, systolic BP, LDL, BMI,
smoking). This score drives:

- **Complication assignment** — the probability of each complication class
  (retinopathy, nephropathy, neuropathy, cardiovascular, or none) via a softmax over
  risk-weighted logits, calibrated so ~65% of patients have no complication.
- **12-month deterioration label** — `P(deteriorate) = sigmoid(z - 1.1 + 0.9 * has_complication)`,
  yielding a ~24% positive rate (a realistic, moderately imbalanced target).

Outpatient visits are generated as a weekly time series with trend, annual
seasonality and noise, split across five clinics; visit intensity is higher for
patients with complications. Clinical notes embed complication-related keywords used
downstream for NLP flag extraction.

## 2. ETL & data quality

- **Quality checks**: per-column completeness, out-of-range detection against schema
  bounds, duplicate keys, and referential integrity to the patient master.
- **Cleaning**: numeric columns cast to float before outlier capping at a configurable
  z-threshold; missing values imputed (median strategy by default); categorical
  normalisation.
- **NLP**: keyword flags (renal, cardiac, referral, poor glycaemic control) parsed from
  free-text notes into binary features.

## 3. Feature engineering

The feature store joins demographics, latest labs, 12-month visit aggregates
(count, average duration, no-show rate, urgent-visit count) and NLP flags, plus
engineered variables: age buckets, a weighted **comorbidity index**, and a
**flag burden** aggregate. Target: `deteriorated_12m`.

## 4. Predictive modelling

- **Task**: binary classification of 12-month patient deterioration.
- **Leakage control**: the raw `complication` label is *excluded* from features.
- **Feature selection**: mutual information ranking plus VIF-based collinearity review.
- **Models**: Logistic Regression, Random Forest, Gradient Boosting, XGBoost
  (with `scale_pos_weight` for imbalance), each tuned via `GridSearchCV` (scoring=F1).
- **Split**: 70/15/15 stratified train/validation/test.
- **Evaluation**: accuracy, precision, recall, F1, ROC-AUC, PR-AUC; confusion
  matrices, ROC/PR curves, feature-importance plots.
- **Calibration**: isotonic calibration wraps the selected model; risk bands are
  Low (<0.20), Medium (0.20-0.50), High (>=0.50).

### Test-set results

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC |
|---|---|---|---|---|---|---|
| Logistic Regression | 0.795 | 0.554 | 0.815 | **0.659** | **0.875** | 0.745 |
| Random Forest | 0.803 | 0.573 | 0.753 | 0.651 | 0.859 | 0.713 |
| Gradient Boosting | 0.844 | 0.722 | 0.582 | 0.645 | 0.865 | 0.727 |
| XGBoost | 0.789 | 0.546 | 0.783 | 0.644 | 0.868 | 0.738 |

The best model by F1 (logistic regression) is registered as the production model.
ROC-AUC ~0.87 reflects a genuinely learnable but non-trivial task — appropriate for
a realistic clinical-risk setting.

## 5. Load forecasting & resource planning

- **Forecast**: an ensemble of Facebook Prophet and seasonal SARIMA (weighted 0.6/0.4)
  over a 12-week horizon, with a 95% interval and automatic fallback to SARIMA-only or
  a seasonal-naive model.
- **Resource scheduling**: forecast demand is converted to role staffing using
  productivity ratios (80 visits/physician, 120/nurse, 200/admin per week), a 15%
  surge buffer is applied, utilisation is projected and weeks above the 85% threshold
  are flagged as high-load.
- **Cost optimisation**: a PuLP/CBC integer linear program minimises weekly staffing
  cost (physician $3,800, nurse $2,100, admin $1,200 per week) subject to covering the
  surge-buffered demand for each role.
- **Reporting**: a self-contained HTML dashboard and a Markdown resource-plan report.

## 6. Reproducibility

All randomness is seeded (default 42). Configuration is centralised in
`config/config.yaml`. Running `python main.py --pipeline full` regenerates every
artefact deterministically.
