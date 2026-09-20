"""
Feature engineering: assemble the modelling-ready feature store and the weekly
outpatient demand time-series from the curated processed tables.

Outputs (written to ``data/feature_store``)
------------------------------------------
  features.parquet          - wide patient-level feature matrix + targets
  weekly_demand.parquet     - weekly outpatient visit counts (overall + by clinic)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.common import get_logger, get_paths

logger = get_logger("feature_engineering")


def _age_bucket(age: int) -> str:
    if age < 45:
        return "<45"
    if age < 55:
        return "45-54"
    if age < 65:
        return "55-64"
    if age < 75:
        return "65-74"
    return "75+"


def _comorbidity_index(row) -> float:
    """Simple additive comorbidity/severity index from labs & vitals."""
    idx = 0.0
    idx += max(0, row["hba1c"] - 7.0) * 0.8
    idx += max(0, row["systolic_bp"] - 130) * 0.02
    idx += max(0, row["ldl"] - 100) * 0.01
    idx += max(0, 90 - row["egfr"]) * 0.03
    idx += min(row["urine_acr"], 1000) * 0.002
    idx += max(0, row["bmi"] - 30) * 0.05
    return round(idx, 3)


def build_feature_store() -> dict:
    paths = get_paths()
    proc = paths["data_processed"]

    patients = pd.read_csv(proc / "patients_clean.csv")
    labs = pd.read_csv(proc / "labs_clean.csv")
    visits = pd.read_csv(proc / "visits_clean.csv", parse_dates=["visit_date"])
    note_flags = pd.read_csv(proc / "note_flags.csv")
    comps = pd.read_csv(proc / "complications_clean.csv")

    # ---- patient-level visit aggregates (last 12 months) ----
    max_date = visits["visit_date"].max()
    cutoff = max_date - pd.Timedelta(days=365)
    recent = visits[visits["visit_date"] >= cutoff]
    visit_agg = recent.groupby("patient_id").agg(
        visit_count_12m=("encounter_id", "count"),
        no_show_rate=("no_show", "mean"),
        avg_duration=("duration_min", "mean"),
        n_urgent=("visit_type", lambda s: (s == "urgent").sum()),
    ).reset_index()

    # ---- merge everything ----
    df = patients.merge(labs, on="patient_id", how="left")
    df = df.merge(visit_agg, on="patient_id", how="left")
    df = df.merge(note_flags, on="patient_id", how="left")
    df = df.merge(comps, on="patient_id", how="left")

    # fill visit/flag gaps
    for c in ["visit_count_12m", "no_show_rate", "avg_duration", "n_urgent"]:
        df[c] = df[c].fillna(0)
    flag_cols = [c for c in df.columns if c.startswith("flag_")]
    df[flag_cols] = df[flag_cols].fillna(0).astype(int)

    # ---- engineered features ----
    df["age_group"] = df["age"].apply(_age_bucket)
    df["comorbidity_index"] = df.apply(_comorbidity_index, axis=1)
    df["pulse_pressure"] = df["systolic_bp"] - df["diastolic_bp"]
    df["tg_hdl_ratio"] = (df["triglycerides"] / df["hdl"].replace(0, np.nan)).round(2)
    df["ckd_flag"] = (df["egfr"] < 60).astype(int)
    df["obese_flag"] = (df["bmi"] >= 30).astype(int)
    df["poor_glycemic_flag"] = (df["hba1c"] >= 8.0).astype(int)
    df["hypertension_flag"] = (df["systolic_bp"] >= 140).astype(int)
    df["albuminuria_flag"] = (df["urine_acr"] >= 30).astype(int)
    df["high_risk_ldl_flag"] = (df["ldl"] >= 130).astype(int)
    df["flag_burden"] = df[flag_cols].sum(axis=1)
    df["deteriorated_12m"] = df["deteriorated_12m"].astype(int)

    paths["feature_store"].mkdir(parents=True, exist_ok=True)
    fp = paths["feature_store"] / "features.parquet"
    df.to_parquet(fp, index=False)
    logger.info("Feature store written: %s (%d rows x %d cols)", fp, len(df), df.shape[1])

    # ---- weekly outpatient demand series ----
    weekly = _build_weekly_demand(visits, paths)

    return {"features": df, "weekly_demand": weekly}


def _build_weekly_demand(visits: pd.DataFrame, paths) -> pd.DataFrame:
    v = visits[~visits["no_show"].astype(bool)].copy()
    v["week"] = v["visit_date"].dt.to_period("W").apply(lambda p: p.start_time)
    overall = v.groupby("week").size().rename("total_visits")
    by_clinic = v.groupby(["week", "clinic"]).size().unstack(fill_value=0)
    weekly = pd.concat([overall, by_clinic], axis=1).reset_index().sort_values("week")
    # drop the first/last partial weeks to stabilise the series
    if len(weekly) > 4:
        weekly = weekly.iloc[1:-1].reset_index(drop=True)
    fp = paths["feature_store"] / "weekly_demand.parquet"
    weekly.to_parquet(fp, index=False)
    weekly.to_csv(paths["feature_store"] / "weekly_demand.csv", index=False)
    logger.info("Weekly demand series written: %s (%d weeks)", fp, len(weekly))
    return weekly


if __name__ == "__main__":
    build_feature_store()
