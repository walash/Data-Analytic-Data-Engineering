"""Shared constants, paths, and data-loading helpers for the modeling subpackage.

Centralises the feature schema (which columns are treated as numeric vs.
categorical), the CVD risk-level target encoding, project paths, and the
stratified train/validation/test split used across the modeling modules.

The prediction target for this module is the derived ``risk_level`` bucket:

    Low = 0, Medium = 1, High = 2

To avoid target leakage we deliberately exclude the ``patient_id`` identifier,
the raw binary ``target`` (which is sampled from the same latent risk that
``risk_level`` is bucketed from), the redundant ``*_z`` standardized copies of
numeric columns, and the pre-expanded one-hot columns (``cp_*``, ``ecg_*``,
``slope_*``, ``thal_1/2/3``).  The pipeline performs its own scaling and
one-hot encoding so the raw coded columns are used instead.
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
FEATURE_STORE_DIR = os.path.join(DATA_DIR, "feature_store")
FEATURES_PARQUET = os.path.join(FEATURE_STORE_DIR, "features.parquet")

MODELS_DIR = os.path.join(PROJECT_ROOT, "models")
OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
FIGURES_DIR = os.path.join(OUTPUTS_DIR, "figures")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")

REGISTRY_PATH = os.path.join(MODELS_DIR, "model_registry.json")


def ensure_dirs() -> None:
    """Create the model/output directories if they do not already exist."""
    for d in (MODELS_DIR, OUTPUTS_DIR, FIGURES_DIR, REPORTS_DIR):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------- #
# Target encoding
# --------------------------------------------------------------------------- #
TARGET_COL = "risk_level"
RISK_ORDER: List[str] = ["Low", "Medium", "High"]
RISK_TO_INT: Dict[str, int] = {"Low": 0, "Medium": 1, "High": 2}
INT_TO_RISK: Dict[int, str] = {v: k for k, v in RISK_TO_INT.items()}
RISK_PALETTE = {"Low": "#2ca02c", "Medium": "#ff7f0e", "High": "#d62728"}

RANDOM_STATE = 42

# --------------------------------------------------------------------------- #
# Feature schema
# --------------------------------------------------------------------------- #
# Continuous / numeric predictors -> StandardScaler in the pipeline.
NUMERIC_FEATURES: List[str] = [
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
    "note_count",
    "avg_note_length",
    "ed_visit_count",
    "ed_avg_los_hours",
    "ed_high_acuity_visits",
    "ed_avg_resources",
]

# Categorical / coded predictors -> OneHotEncoder in the pipeline.
CATEGORICAL_FEATURES: List[str] = [
    "sex",
    "chest_pain_type",
    "fasting_blood_sugar",
    "rest_ecg",
    "exercise_angina",
    "st_slope",
    "thal",
    "mentions_chest_pain",
    "mentions_dyspnea",
    "mentions_diabetes",
    "mentions_smoking",
    "mentions_syncope",
    "age_group",
    "bp_category",
    "cholesterol_category",
]

ALL_FEATURES: List[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Columns explicitly excluded from modeling (identifiers / leakage / redundant).
LEAKAGE_COLS = ["patient_id", "target", "risk_level"]


# --------------------------------------------------------------------------- #
# Data loading & splitting
# --------------------------------------------------------------------------- #
def load_feature_frame(path: str = FEATURES_PARQUET) -> pd.DataFrame:
    """Load the model-ready feature store as a pandas DataFrame."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Feature store not found at {path}. Run the data engineering "
            "pipeline first (src/data_engineering)."
        )
    df = pd.read_parquet(path)
    missing = [c for c in ALL_FEATURES + [TARGET_COL] if c not in df.columns]
    if missing:
        raise KeyError(f"Feature store is missing expected columns: {missing}")
    return df


def get_X_y(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """Split a feature frame into the model matrix ``X`` and integer target ``y``."""
    X = df[ALL_FEATURES].copy()
    y = df[TARGET_COL].map(RISK_TO_INT).astype(int)
    return X, y


def stratified_splits(
    df: pd.DataFrame,
    test_size: float = 0.15,
    val_size: float = 0.15,
    random_state: int = RANDOM_STATE,
) -> Dict[str, Tuple[pd.DataFrame, pd.Series]]:
    """Return stratified train/validation/test splits (default 70/15/15).

    Returns a dict with keys ``train``, ``val``, ``test`` each mapping to an
    ``(X, y)`` tuple.
    """
    from sklearn.model_selection import train_test_split

    X, y = get_X_y(df)

    # First carve out the test set.
    X_temp, X_test, y_temp, y_test = train_test_split(
        X, y, test_size=test_size, stratify=y, random_state=random_state
    )
    # Then split the remainder into train / validation.
    val_relative = val_size / (1.0 - test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_temp, y_temp, test_size=val_relative, stratify=y_temp,
        random_state=random_state,
    )
    return {
        "train": (X_train, y_train),
        "val": (X_val, y_val),
        "test": (X_test, y_test),
    }


def _to_string_frame(X):
    """Cast every column to a plain Python string (module-level for pickling).

    Ensures the OneHotEncoder always sees a uniform string dtype so mixed
    int/str categorical inputs (e.g. a coded ``sex=1`` alongside a string
    ``age_group='60-69'``) and unseen fill values are handled consistently.
    """
    import pandas as pd

    return pd.DataFrame(X).astype(str)


def build_preprocessor(
    numeric: List[str] = None, categorical: List[str] = None
):
    """Construct the ColumnTransformer used by every model pipeline.

    * ``StandardScaler`` for numeric features
    * ``OneHotEncoder`` (dense, ignore-unknown) for categorical features, with
      an up-front string cast so mixed-type coded columns are handled safely.
    """
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

    numeric = numeric if numeric is not None else NUMERIC_FEATURES
    categorical = categorical if categorical is not None else CATEGORICAL_FEATURES

    # Handle both older and newer sklearn OneHotEncoder signatures.
    try:
        ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # pragma: no cover - older sklearn
        ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)

    cat_pipeline = Pipeline(
        steps=[
            ("to_str", FunctionTransformer(_to_string_frame, feature_names_out="one-to-one")),
            ("ohe", ohe),
        ]
    )

    return ColumnTransformer(
        transformers=[
            ("num", StandardScaler(), numeric),
            ("cat", cat_pipeline, categorical),
        ],
        remainder="drop",
    )


def get_feature_names_out(preprocessor) -> List[str]:
    """Return expanded feature names after a fitted preprocessor transform."""
    try:
        return list(preprocessor.get_feature_names_out())
    except Exception:  # pragma: no cover - fallback
        return [f"f{i}" for i in range(preprocessor.transform(
            preprocessor._feature_names_in).shape[1])]


def class_weight_dict(y) -> Dict[int, float]:
    """Compute balanced class weights for the integer target."""
    from sklearn.utils.class_weight import compute_class_weight

    classes = np.unique(y)
    weights = compute_class_weight("balanced", classes=classes, y=y)
    return {int(c): float(w) for c, w in zip(classes, weights)}
