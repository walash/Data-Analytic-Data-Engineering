"""
Train and tune deterioration-prediction classifiers.

Trains Logistic Regression, Random Forest, XGBoost and Gradient Boosting inside
sklearn Pipelines (shared preprocessing) using GridSearchCV, then persists the
fitted pipelines and the held-out test split for evaluation.
"""
from __future__ import annotations

import time

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GridSearchCV
from sklearn.pipeline import Pipeline
from xgboost import XGBClassifier

from src.modeling.common import build_preprocessor, get_splits, load_modeling_frame
from src.utils.common import get_logger, get_paths, load_config

logger = get_logger("model_trainer")


def _model_grid(cfg):
    m = cfg["modeling"]["models"]
    seed = cfg["project"]["random_seed"]
    scale_pos = None  # set per-fit
    return {
        "logistic_regression": (
            LogisticRegression(max_iter=2000, class_weight="balanced"),
            {"clf__C": m["logistic_regression"]["C"],
             "clf__penalty": m["logistic_regression"]["penalty"]},
        ),
        "random_forest": (
            RandomForestClassifier(class_weight="balanced", random_state=seed, n_jobs=-1),
            {"clf__n_estimators": m["random_forest"]["n_estimators"],
             "clf__max_depth": m["random_forest"]["max_depth"],
             "clf__min_samples_leaf": m["random_forest"]["min_samples_leaf"]},
        ),
        "xgboost": (
            XGBClassifier(eval_metric="logloss", random_state=seed, n_jobs=-1,
                          tree_method="hist"),
            {"clf__n_estimators": m["xgboost"]["n_estimators"],
             "clf__max_depth": m["xgboost"]["max_depth"],
             "clf__learning_rate": m["xgboost"]["learning_rate"]},
        ),
        "gradient_boosting": (
            GradientBoostingClassifier(random_state=seed),
            {"clf__n_estimators": m["gradient_boosting"]["n_estimators"],
             "clf__max_depth": m["gradient_boosting"]["max_depth"],
             "clf__learning_rate": m["gradient_boosting"]["learning_rate"]},
        ),
    }


class ModelTrainer:
    def __init__(self):
        self.cfg = load_config()
        self.paths = get_paths()

    def run(self) -> dict:
        logger.info("=== Model training start ===")
        df = load_modeling_frame()
        X_train, X_val, X_test, y_train, y_val, y_test = get_splits(df)
        logger.info("Split sizes: train=%d val=%d test=%d | positive rate=%.3f",
                    len(X_train), len(X_val), len(X_test), y_train.mean())

        pre = build_preprocessor(df)
        cv = self.cfg["modeling"]["cv_folds"]
        results = {}

        for name, (estimator, grid) in _model_grid(self.cfg).items():
            t0 = time.time()
            # handle imbalance for xgboost via scale_pos_weight
            if name == "xgboost":
                spw = (y_train == 0).sum() / max(1, (y_train == 1).sum())
                estimator.set_params(scale_pos_weight=spw)
            pipe = Pipeline([("pre", pre), ("clf", estimator)])
            gs = GridSearchCV(pipe, grid, scoring="f1", cv=cv, n_jobs=-1, refit=True)
            gs.fit(X_train, y_train)
            elapsed = time.time() - t0

            val_f1 = gs.score(X_val, y_val)
            model_path = self.paths["models"] / f"{name}.pkl"
            joblib.dump(gs.best_estimator_, model_path)
            results[name] = {
                "best_params": gs.best_params_,
                "cv_f1": round(gs.best_score_, 4),
                "val_f1": round(val_f1, 4),
                "train_seconds": round(elapsed, 1),
                "model_path": str(model_path),
            }
            logger.info("%-20s cv_f1=%.4f val_f1=%.4f (%.1fs)",
                        name, gs.best_score_, val_f1, elapsed)

        # persist test split for the evaluator
        joblib.dump({"X_test": X_test, "y_test": y_test,
                     "X_val": X_val, "y_val": y_val},
                    self.paths["models"] / "test_split.pkl")
        logger.info("=== Model training done ===")
        return results


if __name__ == "__main__":
    ModelTrainer().run()
