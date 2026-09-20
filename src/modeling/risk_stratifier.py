"""Inference interface for CVD risk stratification.

:class:`RiskStratifier` is the production-facing entry point used by the ED
Capacity Planning module.  It loads the best trained model (as selected by the
:class:`ModelRegistry`), optionally wraps it in a calibrated classifier for
well-behaved probability outputs, and exposes:

* :meth:`predict_risk` - single-patient inference from a plain ``dict``
* :meth:`batch_predict` - vectorised inference over a DataFrame

Both return the risk label (Low/Medium/High) plus per-class probability scores.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import common
from .model_registry import ModelRegistry

logger = logging.getLogger(__name__)

CALIBRATED_PATH = os.path.join(common.MODELS_DIR, "best_model_calibrated.pkl")


class RiskStratifier:
    """Load the best CVD model and provide calibrated risk predictions."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        calibrate: bool = True,
        calibration_method: str = "isotonic",
    ):
        import joblib

        self.registry = ModelRegistry()
        if model_path is None:
            model_path = self.registry.get_best_model_path()
        if model_path is None or not os.path.exists(model_path):
            raise FileNotFoundError(
                "No best model available. Train models first (ModelTrainer)."
            )
        self.model_path = model_path
        self.base_model = joblib.load(model_path)
        best = self.registry.get_best_model()
        self.model_name = best["name"] if best else os.path.basename(model_path)
        logger.info("RiskStratifier loaded model: %s", self.model_name)

        self.calibration_method = calibration_method
        self.model = self.base_model
        self.calibrated = False
        if calibrate:
            self._ensure_calibrated()

    # ------------------------------------------------------------------ #
    # Calibration
    # ------------------------------------------------------------------ #
    def _ensure_calibrated(self) -> None:
        """Load or fit a CalibratedClassifierCV wrapping the best pipeline."""
        import joblib

        if os.path.exists(CALIBRATED_PATH):
            try:
                self.model = joblib.load(CALIBRATED_PATH)
                self.calibrated = True
                logger.info("Loaded calibrated model from %s", CALIBRATED_PATH)
                return
            except Exception:  # pragma: no cover
                logger.warning("Failed to load calibrated model; refitting.")

        self.fit_calibration()

    def fit_calibration(self) -> None:
        """Fit CalibratedClassifierCV on the training split and cache it."""
        import joblib
        from sklearn.base import clone
        from sklearn.calibration import CalibratedClassifierCV

        logger.info("Fitting calibrated classifier (%s) ...", self.calibration_method)
        df = common.load_feature_frame()
        splits = common.stratified_splits(df)
        X_train, y_train = splits["train"]

        base = clone(self.base_model)
        calibrated = CalibratedClassifierCV(
            base, method=self.calibration_method, cv=5,
        )
        calibrated.fit(X_train, y_train)
        self.model = calibrated
        self.calibrated = True
        joblib.dump(calibrated, CALIBRATED_PATH)
        logger.info("Calibrated model saved to %s", CALIBRATED_PATH)

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #
    def _prepare_frame(self, data: pd.DataFrame) -> pd.DataFrame:
        """Ensure all expected feature columns exist (fill sensible defaults)."""
        X = data.copy()
        for col in common.ALL_FEATURES:
            if col not in X.columns:
                # Numeric -> 0.0, categorical -> a neutral placeholder.
                if col in common.NUMERIC_FEATURES:
                    X[col] = 0.0
                else:
                    X[col] = "missing"
        return X[common.ALL_FEATURES]

    def predict_risk(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """Predict CVD risk for a single patient supplied as a dict.

        Returns a dict with ``risk_level`` (Low/Medium/High), the integer
        ``risk_code``, and ``probabilities`` per class.
        """
        frame = pd.DataFrame([patient_data])
        X = self._prepare_frame(frame)
        proba = self.model.predict_proba(X)[0]
        code = int(np.argmax(proba))
        return {
            "risk_level": common.INT_TO_RISK[code],
            "risk_code": code,
            "probabilities": {
                common.INT_TO_RISK[i]: float(proba[i]) for i in range(len(proba))
            },
            "model": self.model_name,
            "calibrated": self.calibrated,
        }

    def batch_predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Predict risk for every row of a DataFrame.

        Returns a copy of ``df`` with added ``risk_level`` and
        ``prob_Low`` / ``prob_Medium`` / ``prob_High`` columns.
        """
        X = self._prepare_frame(df)
        proba = self.model.predict_proba(X)
        codes = np.argmax(proba, axis=1)

        out = df.copy()
        out["risk_level"] = [common.INT_TO_RISK[int(c)] for c in codes]
        for i, label in common.INT_TO_RISK.items():
            out[f"prob_{label}"] = proba[:, i]
        return out

    def get_risk_distribution(self, df: pd.DataFrame) -> pd.Series:
        """Convenience helper returning the predicted risk-level distribution."""
        preds = self.batch_predict(df)
        return preds["risk_level"].value_counts().reindex(
            common.RISK_ORDER, fill_value=0
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    strat = RiskStratifier(calibrate=True)

    # Demonstrate single-patient inference.
    example = {
        "age": 63, "sex": 1, "chest_pain_type": 3, "resting_bp": 145,
        "cholesterol": 260, "fasting_blood_sugar": 1, "rest_ecg": 2,
        "max_heart_rate": 120, "exercise_angina": 1, "st_depression": 2.6,
        "st_slope": 1, "ca_vessels": 2, "thal": 3, "comorbidity_index": 3,
        "hr_reserve_ratio": 0.5, "framingham_risk_score": 18.0,
        "ten_year_cvd_risk_pct": 22.0, "note_count": 3, "avg_note_length": 240.0,
        "ed_visit_count": 2, "ed_avg_los_hours": 6.0, "ed_high_acuity_visits": 1,
        "ed_avg_resources": 3.0, "age_group": "60-69", "bp_category": "Stage2",
        "cholesterol_category": "High",
        "mentions_chest_pain": 1, "mentions_dyspnea": 1, "mentions_diabetes": 1,
        "mentions_smoking": 0, "mentions_syncope": 0,
    }
    result = strat.predict_risk(example)
    print("\nSingle-patient prediction:")
    print(f"  Risk level : {result['risk_level']}")
    print(f"  Probabilities: {result['probabilities']}")

    # Demonstrate batch inference on a sample of the feature store.
    df = common.load_feature_frame().sample(5, random_state=0)
    preds = strat.batch_predict(df.drop(columns=["risk_level"]))
    print("\nBatch prediction (5 rows):")
    print(preds[["patient_id", "risk_level", "prob_Low", "prob_Medium",
                 "prob_High"]].to_string(index=False))


if __name__ == "__main__":
    main()
