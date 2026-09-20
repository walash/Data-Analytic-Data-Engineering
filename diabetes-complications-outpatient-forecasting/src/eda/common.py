"""Shared helpers for the EDA package: data loading and plotting style."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from src.utils.common import get_paths

NUMERIC_FEATURES = [
    "age", "bmi", "hba1c", "fasting_glucose", "systolic_bp", "diastolic_bp",
    "ldl", "hdl", "triglycerides", "creatinine", "egfr", "urine_acr",
    "years_since_diagnosis", "comorbidity_index", "pulse_pressure",
    "tg_hdl_ratio", "visit_count_12m", "no_show_rate", "flag_burden",
]
CATEGORICAL_FEATURES = ["sex", "smoking_status", "insurance_type", "region", "age_group"]
FLAG_FEATURES = ["ckd_flag", "obese_flag", "poor_glycemic_flag", "hypertension_flag",
                 "albuminuria_flag", "high_risk_ldl_flag"]


def set_style() -> None:
    sns.set_theme(style="whitegrid", context="talk")
    plt.rcParams["figure.dpi"] = 110
    plt.rcParams["savefig.dpi"] = 300
    plt.rcParams["savefig.bbox"] = "tight"


def load_features() -> pd.DataFrame:
    fp = get_paths()["feature_store"] / "features.parquet"
    if not fp.exists():
        raise FileNotFoundError(f"{fp} not found. Run the data engineering pipeline first.")
    return pd.read_parquet(fp)


def load_weekly_demand() -> pd.DataFrame:
    fp = get_paths()["feature_store"] / "weekly_demand.parquet"
    return pd.read_parquet(fp)


def savefig(fig, name: str) -> str:
    out = get_paths()["figures"] / name
    fig.savefig(out)
    plt.close(fig)
    return str(out)
