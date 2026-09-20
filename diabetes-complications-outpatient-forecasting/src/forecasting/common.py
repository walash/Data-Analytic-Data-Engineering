"""Shared helpers for the outpatient load-forecasting package."""
from __future__ import annotations

import pandas as pd

from src.utils.common import get_paths, load_config

CLINICS = ["endocrinology", "primary_care", "cardiology", "nephrology", "ophthalmology"]


def load_weekly_demand() -> pd.DataFrame:
    fp = get_paths()["feature_store"] / "weekly_demand.parquet"
    if not fp.exists():
        raise FileNotFoundError(f"{fp} not found. Run the data engineering pipeline first.")
    df = pd.read_parquet(fp)
    df["week"] = pd.to_datetime(df["week"])
    return df.sort_values("week").reset_index(drop=True)


def forecast_config() -> dict:
    return load_config()["forecasting"]


def scheduling_config() -> dict:
    return load_config()["scheduling"]
