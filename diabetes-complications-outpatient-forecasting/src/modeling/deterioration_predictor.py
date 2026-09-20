"""
Production inference interface for patient-deterioration prediction.

Loads the best registered model, wraps it with probability calibration, and
exposes ``predict`` (single patient dict) and ``batch_predict`` (DataFrame),
returning a calibrated deterioration probability and a Low/Medium/High risk band.
"""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV

from src.modeling.common import (CATEGORICAL_FEATURES, feature_columns,
                                 load_modeling_frame)
from src.modeling.model_registry import ModelRegistry
from src.utils.common import get_logger, get_paths

logger = get_logger("deterioration_predictor")

RISK_BANDS = [(0.0, 0.20, "Low"), (0.20, 0.50, "Medium"), (0.50, 1.01, "High")]


def _band(p: float) -> str:
    for lo, hi, label in RISK_BANDS:
        if lo <= p < hi:
            return label
    return "High"


class DeteriorationPredictor:
    def __init__(self, calibrate: bool = True):
        self.paths = get_paths()
        best = ModelRegistry().get_best_model()
        if best is None:
            raise RuntimeError("No best model registered. Run training + registry first.")
        self.model_name = best["name"]
        self.model = joblib.load(best["artifact"])
        self.calibrated = None
        if calibrate:
            self._fit_calibration()

    def _fit_calibration(self):
        """Calibrate on the held-out validation split for reliable probabilities."""
        split_fp = self.paths["models"] / "test_split.pkl"
        if not split_fp.exists():
            logger.warning("No validation split found; skipping calibration")
            return
        split = joblib.load(split_fp)
        X_val, y_val = split["X_val"], split["y_val"]
        try:
            cal = CalibratedClassifierCV(self.model, method="isotonic", cv="prefit")
            cal.fit(X_val, y_val)
            self.calibrated = cal
            logger.info("Fitted isotonic calibration for %s", self.model_name)
        except Exception as exc:  # pragma: no cover
            logger.warning("Calibration failed (%s); using raw model", exc)

    def _estimator(self):
        return self.calibrated if self.calibrated is not None else self.model

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        num, cat, flags = feature_columns(df)
        for c in CATEGORICAL_FEATURES:
            if c in df:
                df[c] = df[c].astype(str)
        return df[num + flags + cat]

    def batch_predict(self, df: pd.DataFrame) -> pd.DataFrame:
        X = self._prepare(df.copy())
        proba = self._estimator().predict_proba(X)[:, 1]
        out = df.copy()
        out["deterioration_proba"] = np.round(proba, 4)
        out["risk_band"] = [_band(p) for p in proba]
        return out

    def predict(self, patient: dict) -> dict:
        df = pd.DataFrame([patient])
        res = self.batch_predict(df).iloc[0]
        return {"deterioration_proba": float(res["deterioration_proba"]),
                "risk_band": res["risk_band"], "model": self.model_name}


if __name__ == "__main__":
    df = load_modeling_frame().head(5)
    pred = DeteriorationPredictor().batch_predict(df)
    print(pred[["patient_id", "deterioration_proba", "risk_band"]].to_string(index=False))
