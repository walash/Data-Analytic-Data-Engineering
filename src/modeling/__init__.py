"""Predictive modeling: cardiovascular risk stratification models.

Public API:

* :class:`ModelTrainer`     - train/tune/persist the model suite
* :class:`ModelEvaluator`   - evaluate, compare, and plot results
* :class:`FeatureSelector`  - RFE / mutual information / VIF selection
* :class:`RiskStratifier`   - inference interface for the ED module
* :class:`ModelRegistry`    - model versioning & metadata registry
"""
from .feature_selector import FeatureSelector
from .model_evaluator import ModelEvaluator
from .model_registry import ModelRegistry
from .model_trainer import ModelTrainer
from .risk_stratifier import RiskStratifier

__all__ = [
    "ModelTrainer",
    "ModelEvaluator",
    "FeatureSelector",
    "RiskStratifier",
    "ModelRegistry",
]
