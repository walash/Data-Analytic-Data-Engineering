"""Shared constants, paths, and data loaders for the ED capacity module.

Centralises project directory resolution, canonical risk orderings, and the
routines that build the daily ED demand time-series (by CVD risk group) that
every downstream component consumes.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
RAW_DIR = os.path.join(DATA_DIR, "raw")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
FEATURE_STORE_DIR = os.path.join(DATA_DIR, "feature_store")

OUTPUTS_DIR = os.path.join(PROJECT_ROOT, "outputs")
FIGURES_DIR = os.path.join(OUTPUTS_DIR, "figures")
REPORTS_DIR = os.path.join(OUTPUTS_DIR, "reports")

ED_TIMESERIES_CSV = os.path.join(FEATURE_STORE_DIR, "ed_demand_timeseries.csv")
ED_TIMESERIES_PARQUET = os.path.join(FEATURE_STORE_DIR, "ed_demand_timeseries.parquet")
ED_VISITS_PROCESSED = os.path.join(PROCESSED_DIR, "ed_visits_processed.csv")
ED_VISITS_RAW = os.path.join(RAW_DIR, "ed_visits.csv")

# --------------------------------------------------------------------------- #
# Canonical orderings / config
# --------------------------------------------------------------------------- #
RISK_ORDER = ["Low", "Medium", "High"]

# Resource intensity per risk group (drives allocation & optimisation).
RESOURCE_INTENSITY = {"Low": 1, "Medium": 2, "High": 3}

# Forecast horizon (days) and training window (months) per the project spec.
FORECAST_HORIZON_DAYS = 60
TRAIN_WINDOW_MONTHS = 22

RISK_COLORS = {"Low": "#2ca02c", "Medium": "#ff7f0e", "High": "#d62728"}


def ensure_dirs() -> None:
    """Create output directories if they do not already exist."""
    for d in (FIGURES_DIR, REPORTS_DIR):
        os.makedirs(d, exist_ok=True)


def _read_timeseries() -> Optional[pd.DataFrame]:
    """Load the pre-built daily ED demand time-series from the feature store."""
    path = None
    if os.path.exists(ED_TIMESERIES_PARQUET):
        path = ED_TIMESERIES_PARQUET
        df = pd.read_parquet(path)
    elif os.path.exists(ED_TIMESERIES_CSV):
        path = ED_TIMESERIES_CSV
        df = pd.read_csv(path)
    else:
        return None
    logger.info("Loaded ED demand time-series from %s (%d rows)", path, len(df))
    df["visit_date"] = pd.to_datetime(df["visit_date"])
    return df


def _build_timeseries_from_visits() -> pd.DataFrame:
    """Fallback: derive daily demand by risk group directly from ED visits.

    Uses the ``RiskStratifier`` when patient features are available; otherwise
    maps the visit ``triage_level`` to a risk group as a heuristic.
    """
    src = ED_VISITS_PROCESSED if os.path.exists(ED_VISITS_PROCESSED) else ED_VISITS_RAW
    logger.info("Building ED demand time-series from visits: %s", src)
    visits = pd.read_csv(src)
    visits["visit_timestamp"] = pd.to_datetime(visits["visit_timestamp"])
    visits["visit_date"] = visits["visit_timestamp"].dt.normalize()

    risk = _assign_risk_to_visits(visits)
    visits["risk_level"] = risk

    grouped = (
        visits.groupby(["risk_level", "visit_date"])
        .agg(
            visits=("visit_id", "count"),
            avg_los=("los_hours", "mean"),
            total_resources=("resources_used", "sum"),
        )
        .reset_index()
    )
    grouped["high_acuity"] = 0.0
    return grouped


def _assign_risk_to_visits(visits: pd.DataFrame) -> pd.Series:
    """Assign a CVD risk group to each ED visit.

    Tries the trained ``RiskStratifier`` on joined patient features; on any
    failure falls back to a triage-level heuristic so the module never breaks.
    """
    try:
        from src.modeling.risk_stratifier import RiskStratifier  # noqa: WPS433
        feats_path = os.path.join(FEATURE_STORE_DIR, "features.csv")
        if os.path.exists(feats_path) and "patient_id" in visits.columns:
            feats = pd.read_csv(feats_path)
            merged = visits.merge(feats, on="patient_id", how="left")
            if merged["age"].notna().any():
                strat = RiskStratifier(calibrate=False)
                preds = strat.batch_predict(merged)
                logger.info("Assigned risk via RiskStratifier.")
                return preds["risk_level"].fillna("Low").values
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.warning("RiskStratifier unavailable (%s); using triage heuristic.", exc)

    # Triage heuristic: 1-2 -> High, 3 -> Medium, 4-5 -> Low.
    triage = visits.get("triage_level", pd.Series([3] * len(visits)))
    return triage.map(lambda t: "High" if t <= 2 else ("Medium" if t == 3 else "Low"))


def load_daily_demand() -> pd.DataFrame:
    """Return a tidy daily demand frame with columns:

    ``risk_level``, ``visit_date``, ``visits`` (and any available extras such
    as ``avg_los``, ``total_resources``).  Rows are sorted and every risk group
    is present for the full date span (missing days filled with zero visits).
    """
    df = _read_timeseries()
    if df is None:
        df = _build_timeseries_from_visits()

    keep = [c for c in ["risk_level", "visit_date", "visits", "avg_los",
                        "total_resources", "high_acuity"] if c in df.columns]
    df = df[keep].copy()
    df["risk_level"] = pd.Categorical(df["risk_level"], categories=RISK_ORDER,
                                      ordered=True)
    df = df.dropna(subset=["risk_level"])

    # Reindex to a complete daily grid per risk group.
    full = []
    date_min, date_max = df["visit_date"].min(), df["visit_date"].max()
    all_days = pd.date_range(date_min, date_max, freq="D")
    for level in RISK_ORDER:
        sub = df[df["risk_level"] == level].set_index("visit_date").sort_index()
        sub = sub.reindex(all_days)
        sub["risk_level"] = level
        sub["visits"] = sub["visits"].fillna(0.0)
        sub.index.name = "visit_date"
        full.append(sub.reset_index())
    out = pd.concat(full, ignore_index=True)
    out = out.sort_values(["risk_level", "visit_date"]).reset_index(drop=True)
    return out


def wide_demand(df: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Pivot daily demand to wide form: index=date, columns=risk groups."""
    if df is None:
        df = load_daily_demand()
    wide = df.pivot_table(index="visit_date", columns="risk_level",
                          values="visits", aggfunc="sum", observed=False)
    wide = wide.reindex(columns=RISK_ORDER).fillna(0.0)
    wide["Total"] = wide[RISK_ORDER].sum(axis=1)
    return wide
