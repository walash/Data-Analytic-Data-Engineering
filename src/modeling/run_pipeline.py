"""End-to-end modeling pipeline runner.

Runs feature selection, model training/tuning, evaluation, and calibration in
sequence, producing all model artifacts and evaluation outputs.

Usage:
    python -m src.modeling.run_pipeline
"""
from __future__ import annotations

import logging

from . import common
from .feature_selector import FeatureSelector
from .model_evaluator import ModelEvaluator
from .model_trainer import ModelTrainer
from .risk_stratifier import RiskStratifier


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    log = logging.getLogger("modeling.pipeline")
    common.ensure_dirs()

    log.info("STEP 1/4 - Feature selection (RFE + MI + VIF)")
    selector = FeatureSelector(top_n=20)
    selected = selector.run()
    log.info("Selected %d features.", len(selected))

    log.info("STEP 2/4 - Model training & hyperparameter tuning")
    trainer = ModelTrainer(search="grid")
    trainer.train_all()

    log.info("STEP 3/4 - Model evaluation & plots")
    evaluator = ModelEvaluator()
    result = evaluator.run()

    log.info("STEP 4/4 - Fit calibrated best-model inference interface")
    strat = RiskStratifier(calibrate=True)
    _ = strat.predict_risk(
        {"age": 60, "sex": 1, "chest_pain_type": 3, "resting_bp": 140,
         "cholesterol": 250, "fasting_blood_sugar": 1, "rest_ecg": 2,
         "max_heart_rate": 130, "exercise_angina": 1, "st_depression": 2.0,
         "st_slope": 1, "ca_vessels": 1, "thal": 3}
    )

    log.info("=" * 60)
    log.info("PIPELINE COMPLETE")
    log.info("Best model: %s", result["best_model"])
    log.info("\n%s", trainer.registry.summary())


if __name__ == "__main__":
    main()
