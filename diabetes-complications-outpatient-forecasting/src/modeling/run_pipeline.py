"""Run the full modelling pipeline: feature selection -> train -> evaluate -> register."""
from __future__ import annotations

from src.modeling.feature_selector import FeatureSelector
from src.modeling.model_evaluator import ModelEvaluator
from src.modeling.model_registry import ModelRegistry
from src.modeling.model_trainer import ModelTrainer
from src.utils.common import get_logger

logger = get_logger("modeling_pipeline")


def run() -> dict:
    logger.info("########## MODELING PIPELINE ##########")
    FeatureSelector().run()
    ModelTrainer().run()
    metrics = ModelEvaluator().run()
    ModelRegistry().register_from_metrics(metrics)
    logger.info("########## MODELING PIPELINE COMPLETE ##########")
    return {"metrics": metrics}


if __name__ == "__main__":
    run()
