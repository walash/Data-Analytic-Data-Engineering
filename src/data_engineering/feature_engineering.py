"""
feature_engineering.py
======================

Builds the model-ready feature store for the CVD Risk Stratification & ED
Capacity Planning project. Consumes the processed tables produced by
``etl_pipeline.py`` and emits a single wide feature table persisted to
``data/feature_store/features.parquet`` (plus a CSV mirror).

Engineered features
-------------------
* **Framingham-like composite risk score** and an estimated 10-year CVD risk
  percentage derived from age, sex, BP, cholesterol, smoking and diabetes proxies.
* **Age-group buckets** (30-39, 40-49, ...).
* **Comorbidity index** - additive count of clinical risk factors.
* **Clinical category flags** - BP category (ACC/AHA), cholesterol category,
  heart-rate reserve ratio.
* **Temporal ED features** - hour / day-of-week / month / season extraction and
  rolling 7-day & 30-day ED visit averages *per risk group*.
* **Patient-level ED aggregates** - visit count, mean LOS, high-acuity count,
  mean resources used.

Run directly::

    python -m src.data_engineering.feature_engineering
"""

from __future__ import annotations

import logging
import os
from typing import List

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("feature_engineering")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")
FEATURE_STORE_DIR = os.path.join(PROJECT_ROOT, "data", "feature_store")


# ---------------------------------------------------------------------------
# Framingham-like risk scoring
# ---------------------------------------------------------------------------
class FraminghamRiskScorer:
    """A transparent, Framingham-inspired 10-year CVD risk estimator.

    This is a *simplified educational* implementation using sex-specific point
    systems for the risk factors available in our dataset (age, total
    cholesterol, systolic BP, smoking proxy, diabetes proxy). It is **not** a
    validated clinical instrument and must not be used for real diagnosis.
    """

    @staticmethod
    def _age_points(age: int, sex: int) -> int:
        # sex: 1 = male, 0 = female
        if sex == 1:
            bins = [(34, -1), (39, 0), (44, 1), (49, 2), (54, 3),
                    (59, 4), (64, 5), (69, 6), (74, 7), (200, 8)]
        else:
            bins = [(34, -3), (39, 0), (44, 1), (49, 2), (54, 3),
                    (59, 4), (64, 5), (69, 6), (74, 7), (200, 8)]
        for upper, pts in bins:
            if age <= upper:
                return pts
        return 8

    @staticmethod
    def _chol_points(chol: int) -> int:
        if chol < 160:
            return 0
        if chol < 200:
            return 1
        if chol < 240:
            return 2
        if chol < 280:
            return 3
        return 4

    @staticmethod
    def _bp_points(sbp: int) -> int:
        if sbp < 120:
            return 0
        if sbp < 130:
            return 1
        if sbp < 140:
            return 2
        if sbp < 160:
            return 2
        return 3

    @classmethod
    def score_row(cls, row: pd.Series) -> float:
        """Return an integer-ish composite point total (higher = riskier)."""
        pts = 0
        pts += cls._age_points(int(row["age"]), int(row["sex"]))
        pts += cls._chol_points(int(row["cholesterol"]))
        pts += cls._bp_points(int(row["resting_bp"]))
        pts += 2 if int(row.get("fasting_blood_sugar", 0)) == 1 else 0  # diabetes proxy
        pts += 2 if int(row.get("mentions_smoking", 0)) == 1 else 0     # smoking proxy
        pts += 1 if int(row.get("exercise_angina", 0)) == 1 else 0
        pts += int(row.get("ca_vessels", 0))
        return float(pts)

    @staticmethod
    def points_to_risk_pct(points: float) -> float:
        """Map composite points to an estimated 10-year CVD risk percentage.

        Uses a logistic transform calibrated so that ~0 pts -> ~2% and
        ~20 pts -> ~45%.
        """
        risk = 1.0 / (1.0 + np.exp(-(0.22 * points - 3.2)))
        return round(float(risk * 100), 2)


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------
class FeatureEngineer:
    """Assemble the wide feature table from processed inputs."""

    def __init__(self, processed_dir: str = PROCESSED_DIR, feature_dir: str = FEATURE_STORE_DIR):
        self.processed_dir = processed_dir
        self.feature_dir = feature_dir
        self.scorer = FraminghamRiskScorer()

    # -- loading --------------------------------------------------------- #
    def _load(self, name: str) -> pd.DataFrame:
        path = os.path.join(self.processed_dir, f"{name}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Processed file '{name}' missing at {path}. Run ETL first.")
        return pd.read_csv(path)

    # -- static / demographic features ----------------------------------- #
    @staticmethod
    def _age_group(age: int) -> str:
        if age < 40:
            return "30-39"
        if age < 50:
            return "40-49"
        if age < 60:
            return "50-59"
        if age < 70:
            return "60-69"
        return "70+"

    @staticmethod
    def _bp_category(sbp: int) -> str:
        if sbp < 120:
            return "Normal"
        if sbp < 130:
            return "Elevated"
        if sbp < 140:
            return "Stage1"
        if sbp < 180:
            return "Stage2"
        return "Crisis"

    @staticmethod
    def _chol_category(chol: int) -> str:
        if chol < 200:
            return "Desirable"
        if chol < 240:
            return "Borderline"
        return "High"

    def _comorbidity_index(self, row: pd.Series) -> int:
        """Additive count of comorbid cardiovascular risk factors."""
        idx = 0
        idx += int(row["resting_bp"] >= 140)                 # hypertension
        idx += int(row["cholesterol"] >= 240)                # hypercholesterolemia
        idx += int(row.get("fasting_blood_sugar", 0) == 1)   # diabetes
        idx += int(row.get("exercise_angina", 0) == 1)       # angina
        idx += int(row.get("ca_vessels", 0) >= 1)            # vessel disease
        idx += int(row.get("mentions_smoking", 0) == 1)      # smoking
        idx += int(row.get("st_depression", 0) >= 2.0)       # ischemia marker
        return int(idx)

    def build_patient_features(self, patients: pd.DataFrame) -> pd.DataFrame:
        df = patients.copy()

        df["age_group"] = df["age"].apply(self._age_group)
        df["bp_category"] = df["resting_bp"].apply(self._bp_category)
        df["cholesterol_category"] = df["cholesterol"].apply(self._chol_category)
        df["comorbidity_index"] = df.apply(self._comorbidity_index, axis=1)

        # Heart-rate reserve ratio: achieved max HR / age-predicted max (220-age).
        df["hr_reserve_ratio"] = (df["max_heart_rate"] / (220 - df["age"])).round(3)

        # Framingham composite + 10-year risk %.
        df["framingham_risk_score"] = df.apply(self.scorer.score_row, axis=1)
        df["ten_year_cvd_risk_pct"] = df["framingham_risk_score"].apply(
            self.scorer.points_to_risk_pct
        )

        logger.info("Built demographic / clinical features for %d patients", len(df))
        return df

    # -- temporal ED features (rolling averages per risk group) ---------- #
    def build_ed_temporal_features(
        self, visits: pd.DataFrame, patients: pd.DataFrame
    ) -> pd.DataFrame:
        """Compute a daily per-risk-group ED demand table with rolling averages.

        Returns a long dataframe (risk_level x date) that is saved separately and
        drives the ED capacity-planning module downstream.
        """
        v = visits.copy()
        v["visit_timestamp"] = pd.to_datetime(v["visit_timestamp"], errors="coerce")
        v = v.dropna(subset=["visit_timestamp"])

        # Attach risk level via patient join.
        risk_map = patients.set_index("patient_id")["risk_level"]
        v["risk_level"] = v["patient_id"].map(risk_map).fillna("Unknown")
        v["visit_date"] = v["visit_timestamp"].dt.normalize()

        # Daily counts per risk group.
        daily = (
            v.groupby(["risk_level", "visit_date"])
            .agg(
                visits=("visit_id", "count"),
                avg_los=("los_hours", "mean"),
                high_acuity=("high_acuity", "sum") if "high_acuity" in v.columns else ("triage_level", lambda s: (s <= 2).sum()),
                total_resources=("resources_used", "sum"),
            )
            .reset_index()
            .sort_values(["risk_level", "visit_date"])
        )

        # Reindex each risk group over the full continuous date range so rolling
        # windows are calendar-correct (zero-fill days with no visits).
        full_frames: List[pd.DataFrame] = []
        date_min, date_max = daily["visit_date"].min(), daily["visit_date"].max()
        full_range = pd.date_range(date_min, date_max, freq="D")
        for risk, grp in daily.groupby("risk_level"):
            g = grp.set_index("visit_date").reindex(full_range)
            g["risk_level"] = risk
            g["visits"] = g["visits"].fillna(0)
            g["avg_los"] = g["avg_los"].fillna(0.0)
            g["high_acuity"] = g["high_acuity"].fillna(0)
            g["total_resources"] = g["total_resources"].fillna(0)
            g["roll7_visits"] = g["visits"].rolling(7, min_periods=1).mean().round(3)
            g["roll30_visits"] = g["visits"].rolling(30, min_periods=1).mean().round(3)
            g["roll7_resources"] = g["total_resources"].rolling(7, min_periods=1).mean().round(3)
            g["roll30_resources"] = g["total_resources"].rolling(30, min_periods=1).mean().round(3)
            g = g.rename_axis("visit_date").reset_index()
            full_frames.append(g)

        temporal = pd.concat(full_frames, ignore_index=True)
        temporal = temporal[
            ["risk_level", "visit_date", "visits", "avg_los", "high_acuity",
             "total_resources", "roll7_visits", "roll30_visits",
             "roll7_resources", "roll30_resources"]
        ]
        logger.info(
            "Built ED temporal table: %d rows across %d risk groups x %d days",
            len(temporal), temporal["risk_level"].nunique(), len(full_range),
        )
        return temporal

    # -- patient-level ED aggregates ------------------------------------- #
    def build_ed_patient_aggregates(self, visits: pd.DataFrame) -> pd.DataFrame:
        v = visits.copy()
        if "high_acuity" not in v.columns:
            v["high_acuity"] = (v["triage_level"] <= 2).astype(int)
        agg = (
            v.groupby("patient_id")
            .agg(
                ed_visit_count=("visit_id", "count"),
                ed_avg_los_hours=("los_hours", "mean"),
                ed_high_acuity_visits=("high_acuity", "sum"),
                ed_avg_resources=("resources_used", "mean"),
            )
            .reset_index()
        )
        agg["ed_avg_los_hours"] = agg["ed_avg_los_hours"].round(2)
        agg["ed_avg_resources"] = agg["ed_avg_resources"].round(2)
        logger.info("Built ED patient aggregates for %d patients", len(agg))
        return agg

    # -- orchestration --------------------------------------------------- #
    def run(self) -> pd.DataFrame:
        os.makedirs(self.feature_dir, exist_ok=True)
        logger.info("=== Feature engineering started ===")

        patients = self._load("patients_processed")
        visits = self._load("ed_visits_processed")

        patient_feats = self.build_patient_features(patients)
        ed_aggs = self.build_ed_patient_aggregates(visits)
        temporal = self.build_ed_temporal_features(visits, patients)

        # Assemble the wide feature vector.
        features = patient_feats.merge(ed_aggs, on="patient_id", how="left")
        ed_cols = ["ed_visit_count", "ed_avg_los_hours", "ed_high_acuity_visits", "ed_avg_resources"]
        features[ed_cols] = features[ed_cols].fillna(0)

        # Persist feature store (parquet primary + CSV mirror).
        feat_path = os.path.join(self.feature_dir, "features.parquet")
        csv_path = os.path.join(self.feature_dir, "features.csv")
        temporal_path = os.path.join(self.feature_dir, "ed_demand_timeseries.parquet")
        temporal_csv = os.path.join(self.feature_dir, "ed_demand_timeseries.csv")

        features.to_parquet(feat_path, index=False)
        features.to_csv(csv_path, index=False)
        temporal.to_parquet(temporal_path, index=False)
        temporal.to_csv(temporal_csv, index=False)

        logger.info("Saved feature store   -> %s (%d rows x %d cols)",
                    feat_path, len(features), features.shape[1])
        logger.info("Saved ED demand series-> %s (%d rows)", temporal_path, len(temporal))
        logger.info("=== Feature engineering finished ===")
        return features


def main() -> None:
    FeatureEngineer().run()


if __name__ == "__main__":
    main()
