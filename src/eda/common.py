"""Shared constants, path helpers, and label maps for the EDA subpackage.

Centralises project paths, human-readable label mappings for the
UCI-style categorical encodings, and the canonical CVD risk-level ordering
used across all EDA modules.
"""
from __future__ import annotations

import os

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))

RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
FEATURE_STORE_DIR = os.path.join(PROJECT_ROOT, "data", "feature_store")

OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
FIGURES_DIR = os.path.join(OUTPUTS_DIR, "figures")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")

FEATURES_PARQUET = os.path.join(FEATURE_STORE_DIR, "features.parquet")
ED_TS_PARQUET = os.path.join(FEATURE_STORE_DIR, "ed_demand_timeseries.parquet")
ED_VISITS_RAW = os.path.join(RAW_DIR, "ed_visits.csv")


def ensure_dirs() -> None:
    """Create the outputs directories if they do not already exist."""
    for d in (OUTPUTS_DIR, FIGURES_DIR, REPORTS_DIR):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------- #
# Canonical orderings
# --------------------------------------------------------------------------- #
RISK_ORDER = ["Low", "Medium", "High"]
RISK_PALETTE = {"Low": "#2ca02c", "Medium": "#ff7f0e", "High": "#d62728"}

# --------------------------------------------------------------------------- #
# Categorical label maps (mirror src/data_engineering/schema.py encodings)
# --------------------------------------------------------------------------- #
SEX_LABELS = {0: "Female", 1: "Male"}
CHEST_PAIN_LABELS = {
    0: "Typical Angina",
    1: "Atypical Angina",
    2: "Non-Anginal",
    3: "Asymptomatic",
}
FBS_LABELS = {0: "FBS \u2264 120 mg/dl", 1: "FBS > 120 mg/dl"}
REST_ECG_LABELS = {0: "Normal", 1: "ST-T Abnormality", 2: "LV Hypertrophy"}
EXERCISE_ANGINA_LABELS = {0: "No", 1: "Yes"}
ST_SLOPE_LABELS = {0: "Upsloping", 1: "Flat", 2: "Downsloping"}
THAL_LABELS = {1: "Normal", 2: "Fixed Defect", 3: "Reversible Defect"}
TARGET_LABELS = {0: "No Disease", 1: "Disease"}

# Columns treated as categorical in the EHR/feature table.
CATEGORICAL_FEATURES = [
    "sex",
    "chest_pain_type",
    "fasting_blood_sugar",
    "rest_ecg",
    "exercise_angina",
    "st_slope",
    "thal",
    "ca_vessels",
    "target",
    "age_group",
    "bp_category",
    "cholesterol_category",
]

# Core numeric clinical features for descriptive stats / tests.
NUMERIC_FEATURES = [
    "age",
    "resting_bp",
    "cholesterol",
    "max_heart_rate",
    "st_depression",
    "ca_vessels",
    "comorbidity_index",
    "hr_reserve_ratio",
    "framingham_risk_score",
    "ten_year_cvd_risk_pct",
    "ed_visit_count",
    "ed_avg_los_hours",
]

# Human readable label map keyed by column name -> {code: label}
LABEL_MAPS = {
    "sex": SEX_LABELS,
    "chest_pain_type": CHEST_PAIN_LABELS,
    "fasting_blood_sugar": FBS_LABELS,
    "rest_ecg": REST_ECG_LABELS,
    "exercise_angina": EXERCISE_ANGINA_LABELS,
    "st_slope": ST_SLOPE_LABELS,
    "thal": THAL_LABELS,
    "target": TARGET_LABELS,
}


def label_series(series, column):
    """Return a copy of *series* with codes mapped to human labels if known."""
    mapping = LABEL_MAPS.get(column)
    if mapping is None:
        return series
    return series.map(lambda v: mapping.get(v, v))


def load_features(path: str = FEATURES_PARQUET):
    """Load the model-ready feature store (patient-level)."""
    import pandas as pd

    df = pd.read_parquet(path)
    if "risk_level" in df.columns:
        import pandas as pd  # noqa: F811

        df["risk_level"] = pd.Categorical(
            df["risk_level"], categories=RISK_ORDER, ordered=True
        )
    return df


def load_ed_timeseries(path: str = ED_TS_PARQUET):
    """Load the ED demand time-series (per risk level per day)."""
    import pandas as pd

    df = pd.read_parquet(path)
    if "visit_date" in df.columns:
        df["visit_date"] = pd.to_datetime(df["visit_date"])
    return df


def load_ed_visits_raw(path: str = ED_VISITS_RAW):
    """Load raw ED visit events (for temporal hour/day/month patterns)."""
    import pandas as pd

    df = pd.read_csv(path, parse_dates=["visit_timestamp"])
    return df
