# Methodology

**Project:** CVD Risk Stratification & ED Capacity Planning System
**Version:** 1.0.0

This document details the scientific and engineering methodology behind each
stage of the pipeline.

> ⚠️ **Disclaimer** — All data is **synthetic** and non-PHI. The Framingham-style
> score is a *simplified educational* implementation, not a validated clinical
> instrument, and must not be used for real diagnosis.

---

## 1. Data Generation Methodology

Synthetic data is produced by `src/data_engineering/data_generator.py` using the
**UCI Heart Disease (Cleveland) encoding** so records are statistically plausible
and interoperable with well-known clinical conventions.

- **Patients (structured EHR):** 10,000 records with age, sex, chest-pain type,
  resting BP, cholesterol, fasting blood sugar, resting ECG, max heart rate,
  exercise-induced angina, ST depression/slope, number of coloured vessels, and
  thalassemia. Values are drawn from sex/age-conditioned distributions so that
  clinically correlated risk factors co-occur realistically.
- **Clinical notes (unstructured):** free-text narratives per patient (admission,
  progress, discharge, consult, nursing) authored with `Faker`, seeded with
  condition-relevant phrasing (chest pain, dyspnea, diabetes, smoking, syncope).
- **ED visits:** arrival timestamps spanning ~2 years, ESI triage levels,
  chief complaints, dispositions, length of stay, and resource counts.
- **Reproducibility:** a fixed RNG seed (`config.yaml → data_generation.seed`).

The binary `target` (heart disease present/absent) and the derived
`risk_level` (Low/Medium/High) are assigned consistently with the underlying risk
factors so downstream supervised learning is well-posed.

---

## 2. Feature Engineering Approach

Implemented in `src/data_engineering/feature_engineering.py`.

### 2.1 Framingham-style risk score
A transparent, sex-specific **point system** approximating the Framingham 10-year
CVD risk model over the available factors:

```
points = age_points(age, sex)
       + cholesterol_points(cholesterol)
       + blood_pressure_points(systolic_bp)
       + 2 * (fasting_blood_sugar == 1)     # diabetes proxy
       + 2 * (mentions_smoking == 1)        # smoking proxy
       + 1 * (exercise_angina == 1)
       + ca_vessels
```

The composite point total is mapped to an estimated 10-year risk percentage via a
calibrated logistic transform:

```
risk_pct = 100 / (1 + exp(-(0.22 * points - 3.2)))
```

so ~0 points ≈ 2% and ~20 points ≈ 45%.

### 2.2 Additional engineered features
- **Comorbidity index** — additive count of hypertension, hypercholesterolemia,
  diabetes, angina, vessel disease, smoking, and ischemia markers.
- **HR-reserve ratio** — `max_heart_rate / (220 − age)`.
- **Categorical buckets** — age group, BP category (Normal/Elevated/Stage1/
  Stage2/Crisis), cholesterol category (Desirable/Borderline/High).
- **NLP flags** — keyword-based binary indicators + note counts / average note
  length extracted from clinical narratives.
- **ED aggregates** — per-patient visit count, mean LOS, high-acuity visits,
  mean resources; plus a daily per-risk-group demand series with 7/30-day rolling
  means for the capacity module.

---

## 3. Model Selection Rationale

Implemented in `src/modeling/`. Four classifiers span the linear-to-nonlinear
spectrum so the simplest adequate model can be chosen:

| Model | Why included |
|-------|--------------|
| Logistic Regression | Fast, interpretable linear baseline |
| Random Forest | Nonlinear bagging, robust to feature scaling |
| XGBoost | Gradient boosting, strong tabular performance |
| Gradient Boosting (sklearn) | Boosting comparator / cross-check |

- **Feature selection:** mutual information + Recursive Feature Elimination (RFE)
  with a VIF check to control multicollinearity, combined into a composite score.
- **Training:** a preprocessing + estimator `Pipeline` (scaling numerics, one-hot
  encoding categoricals) tuned with **stratified 5-fold grid-search CV**.
- **Selection metric:** **macro-F1**, chosen because the risk classes are
  imbalanced (Low ≫ Medium ≫ High) and all three tiers matter clinically.
- **Calibration:** the winning model is probability-calibrated and exposed via
  `RiskStratifier` for inference.
- **Registry:** every model's metrics/params/features are versioned in
  `models/model_registry.json`, with the best model recorded explicitly.

On this dataset **Logistic Regression** won on macro-F1 (≈0.99), reflecting the
strong, largely linear signal introduced by the engineered risk features.

---

## 4. Evaluation Metrics

- **Accuracy** — overall correct-classification rate (context, not sole metric
  under class imbalance).
- **Macro-F1** — unweighted mean of per-class F1; the primary selection metric so
  the minority High-risk class is not ignored.
- **ROC-AUC (One-vs-Rest)** — class-separation quality across thresholds.
- **Confusion matrices** — per-class error structure.
- **Precision-Recall & calibration curves** — reliability of predicted
  probabilities (important for downstream demand forecasting).
- **Learning curve** — bias/variance and data-sufficiency diagnostic.

---

## 5. Forecasting Methodology

Implemented in `src/ed_capacity/demand_forecaster.py`.

- **Inputs:** daily ED visit counts **by CVD risk group** from the feature store.
- **Models:**
  - **Prophet** — additive model with weekly + yearly seasonality and 95%
    intervals.
  - **SARIMA** — `(1,1,1)(1,1,1)₇` seasonal ARIMA (statsmodels).
- **Ensemble:** weighted average **60% Prophet / 40% SARIMA**. SARIMA also serves
  as the **fallback** if Prophet is unavailable in the environment.
- **Horizon:** 60 days; **training window:** trailing ~22 months.
- Forecasts are generated per risk group and recombined, preserving the risk mix
  that drives resource intensity.

---

## 6. Capacity Planning Algorithm

Implemented in `resource_allocator.py` and `capacity_optimizer.py`.

### 6.1 Resource allocation rules
| Risk group | Intensity | Bed type | Key resources |
|------------|-----------|----------|---------------|
| High | 3 | ICU bed | Cardiologist consult, echo, troponin labs |
| Medium | 2 | Monitored bed | ECG, basic labs |
| Low | 1 | Standard ED bay | Basic triage |

A **20% surge buffer** is applied to every bed, staff, equipment, and lab
estimate. Days with capacity utilisation above **85%** are flagged `HIGH_DEMAND`.

### 6.2 Staffing optimisation
A **linear program** (PuLP with the CBC solver) minimises daily staffing cost:

```
minimise   Σ (cost_physician · P_d + cost_nurse · N_d + cost_tech · T_d)
subject to demand-coverage constraints (per role, per day),
           minimum safety-staffing floors,
           surge-adjusted headcount ceilings,
over a rolling 7-day horizon.
```

If PuLP/CBC is unavailable, a closed-form heuristic produces a feasible schedule
so the pipeline still completes. Outputs feed the HTML capacity dashboard and the
Markdown summary report (`outputs/reports/ed_capacity_report.md`).
