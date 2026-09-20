"""Shared modelling helpers: feature lists, preprocessing, data splits."""
from __future__ import annotations

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.utils.common import get_paths, load_config

TARGET = "deteriorated_12m"

NUMERIC_FEATURES = [
    "age", "bmi", "hba1c", "fasting_glucose", "systolic_bp", "diastolic_bp",
    "ldl", "hdl", "triglycerides", "creatinine", "egfr", "urine_acr",
    "years_since_diagnosis", "comorbidity_index", "pulse_pressure", "tg_hdl_ratio",
    "visit_count_12m", "no_show_rate", "avg_duration", "n_urgent", "flag_burden",
]
CATEGORICAL_FEATURES = ["sex", "smoking_status", "insurance_type", "region", "age_group"]
FLAG_FEATURES = ["ckd_flag", "obese_flag", "poor_glycemic_flag", "hypertension_flag",
                 "albuminuria_flag", "high_risk_ldl_flag",
                 "flag_poor_control", "flag_renal", "flag_retinal", "flag_neuro",
                 "flag_cardiac", "flag_referral"]

# NOTE: 'complication' is an OUTCOME and is deliberately excluded to avoid leakage.


def feature_columns(df: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    num = [c for c in NUMERIC_FEATURES if c in df]
    cat = [c for c in CATEGORICAL_FEATURES if c in df]
    flags = [c for c in FLAG_FEATURES if c in df]
    return num, cat, flags


def build_preprocessor(df: pd.DataFrame) -> ColumnTransformer:
    num, cat, flags = feature_columns(df)
    try:
        ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # older sklearn
        ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)
    return ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), num + flags),
            ("cat", ohe, cat),
        ],
        remainder="drop",
    )


def load_modeling_frame() -> pd.DataFrame:
    fp = get_paths()["feature_store"] / "features.parquet"
    if not fp.exists():
        raise FileNotFoundError(f"{fp} not found. Run the data engineering pipeline first.")
    df = pd.read_parquet(fp)
    # ensure categoricals are strings
    for c in CATEGORICAL_FEATURES:
        if c in df:
            df[c] = df[c].astype(str)
    return df


def get_splits(df: pd.DataFrame):
    cfg = load_config()["modeling"]
    seed = load_config()["project"]["random_seed"]
    num, cat, flags = feature_columns(df)
    X = df[num + flags + cat]
    y = df[TARGET].astype(int)
    X_tmp, X_test, y_tmp, y_test = train_test_split(
        X, y, test_size=cfg["test_size"], stratify=y, random_state=seed)
    val_ratio = cfg["val_size"] / (1 - cfg["test_size"])
    X_train, X_val, y_train, y_val = train_test_split(
        X_tmp, y_tmp, test_size=val_ratio, stratify=y_tmp, random_state=seed)
    return X_train, X_val, X_test, y_train, y_val, y_test
