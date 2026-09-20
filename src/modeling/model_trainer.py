"""Model training for CVD risk stratification (multi-class: Low/Medium/High).

Trains four classifiers inside end-to-end preprocessing pipelines with
hyperparameter tuning via cross-validated grid search:

* Logistic Regression (multinomial, L2, ``C`` tuned, ``class_weight='balanced'``)
* Random Forest (``n_estimators``, ``max_depth`` tuned, ``class_weight='balanced'``)
* XGBoost (``multi:softprob``; ``learning_rate``/``max_depth``/``n_estimators``
  tuned; class imbalance handled with SMOTE)
* Gradient Boosting (ensemble comparison; SMOTE for imbalance)

Every fitted pipeline is persisted to ``models/*.pkl`` with joblib and the
training metrics (accuracy, F1-macro, ROC-AUC, training time) are logged and
recorded in the :class:`ModelRegistry`.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from . import common
from .model_registry import ModelRegistry

logger = logging.getLogger(__name__)


class ModelTrainer:
    """Train, tune, and persist the CVD risk-stratification model suite."""

    def __init__(
        self,
        df: Optional[pd.DataFrame] = None,
        search: str = "grid",
        cv_folds: int = 5,
        random_state: int = common.RANDOM_STATE,
    ):
        common.ensure_dirs()
        self.df = df if df is not None else common.load_feature_frame()
        self.search = search
        self.cv_folds = cv_folds
        self.random_state = random_state

        self.splits = common.stratified_splits(self.df, random_state=random_state)
        self.X_train, self.y_train = self.splits["train"]
        self.X_val, self.y_val = self.splits["val"]
        self.X_test, self.y_test = self.splits["test"]

        self.results_: Dict[str, Dict[str, Any]] = {}
        self.best_estimators_: Dict[str, Any] = {}
        self.registry = ModelRegistry()

        logger.info(
            "Split sizes -> train=%d val=%d test=%d",
            len(self.X_train), len(self.X_val), len(self.X_test),
        )

    # ------------------------------------------------------------------ #
    # Pipeline / grid definitions
    # ------------------------------------------------------------------ #
    def _sklearn_pipeline(self, clf):
        from sklearn.pipeline import Pipeline as SkPipeline

        return SkPipeline(
            steps=[
                ("preprocessor", common.build_preprocessor()),
                ("classifier", clf),
            ]
        )

    def _imb_pipeline(self, clf):
        from imblearn.pipeline import Pipeline as ImbPipeline
        from imblearn.over_sampling import SMOTE

        return ImbPipeline(
            steps=[
                ("preprocessor", common.build_preprocessor()),
                ("smote", SMOTE(random_state=self.random_state, k_neighbors=5)),
                ("classifier", clf),
            ]
        )

    def _model_space(self) -> Dict[str, Dict[str, Any]]:
        """Return {name: {pipeline, param_grid}} for each candidate model."""
        from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from xgboost import XGBClassifier

        space: Dict[str, Dict[str, Any]] = {}

        # --- Logistic Regression (multinomial, L2) --------------------- #
        space["logistic_regression"] = {
            "pipeline": self._sklearn_pipeline(
                LogisticRegression(
                    penalty="l2",
                    solver="lbfgs",
                    max_iter=2000,
                    class_weight="balanced",
                    random_state=self.random_state,
                )
            ),
            "param_grid": {
                "classifier__C": [0.05, 0.1, 0.5, 1.0, 2.0],
            },
        }

        # --- Random Forest -------------------------------------------- #
        space["random_forest"] = {
            "pipeline": self._sklearn_pipeline(
                RandomForestClassifier(
                    n_estimators=200,
                    class_weight="balanced",
                    random_state=self.random_state,
                    n_jobs=-1,
                )
            ),
            "param_grid": {
                "classifier__n_estimators": [200, 300],
                "classifier__max_depth": [None, 10, 20],
                "classifier__min_samples_leaf": [1, 3],
            },
        }

        # --- XGBoost (SMOTE for imbalance) ---------------------------- #
        space["xgboost"] = {
            "pipeline": self._imb_pipeline(
                XGBClassifier(
                    objective="multi:softprob",
                    num_class=3,
                    eval_metric="mlogloss",
                    tree_method="hist",
                    random_state=self.random_state,
                    n_jobs=-1,
                )
            ),
            "param_grid": {
                "classifier__n_estimators": [200, 400],
                "classifier__max_depth": [3, 5, 7],
                "classifier__learning_rate": [0.05, 0.1],
            },
        }

        # --- Gradient Boosting (SMOTE for imbalance) ------------------ #
        space["gradient_boosting"] = {
            "pipeline": self._imb_pipeline(
                GradientBoostingClassifier(random_state=self.random_state)
            ),
            "param_grid": {
                "classifier__n_estimators": [150, 250],
                "classifier__max_depth": [3, 5],
                "classifier__learning_rate": [0.05, 0.1],
            },
        }

        return space

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #
    def _make_search(self, pipeline, param_grid):
        from sklearn.model_selection import (
            GridSearchCV,
            RandomizedSearchCV,
            StratifiedKFold,
        )

        cv = StratifiedKFold(
            n_splits=self.cv_folds, shuffle=True, random_state=self.random_state
        )
        if self.search == "random":
            return RandomizedSearchCV(
                pipeline,
                param_distributions=param_grid,
                n_iter=min(10, int(np.prod([len(v) for v in param_grid.values()]))),
                scoring="f1_macro",
                cv=cv,
                n_jobs=-1,
                random_state=self.random_state,
                refit=True,
                verbose=0,
            )
        return GridSearchCV(
            pipeline,
            param_grid=param_grid,
            scoring="f1_macro",
            cv=cv,
            n_jobs=-1,
            refit=True,
            verbose=0,
        )

    def train_one(self, name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Tune + fit a single model, evaluate on validation, and persist it."""
        from sklearn.metrics import accuracy_score, f1_score, roc_auc_score

        logger.info("=== Training %s ===", name)
        search = self._make_search(spec["pipeline"], spec["param_grid"])

        start = time.time()
        search.fit(self.X_train, self.y_train)
        train_time = time.time() - start

        best = search.best_estimator_
        self.best_estimators_[name] = best

        # Validation-set metrics (for model-selection signal).
        y_val_pred = best.predict(self.X_val)
        val_proba = best.predict_proba(self.X_val)
        val_acc = accuracy_score(self.y_val, y_val_pred)
        val_f1 = f1_score(self.y_val, y_val_pred, average="macro")
        try:
            val_auc = roc_auc_score(
                self.y_val, val_proba, multi_class="ovr", average="macro"
            )
        except ValueError:
            val_auc = float("nan")

        # Persist the fitted pipeline.
        artifact_path = os.path.join(common.MODELS_DIR, f"{name}.pkl")
        self._save(best, artifact_path)

        metrics = {
            "cv_best_f1_macro": float(search.best_score_),
            "val_accuracy": float(val_acc),
            "val_f1_macro": float(val_f1),
            "val_roc_auc_ovr": float(val_auc),
            "train_time_sec": float(train_time),
        }
        result = {
            "name": name,
            "best_params": search.best_params_,
            "metrics": metrics,
            "artifact_path": artifact_path,
        }
        self.results_[name] = result

        logger.info(
            "%s | cv_f1=%.4f val_acc=%.4f val_f1=%.4f val_auc=%.4f | %.1fs",
            name, metrics["cv_best_f1_macro"], val_acc, val_f1, val_auc, train_time,
        )
        logger.info("%s best params: %s", name, search.best_params_)

        # Register the model version.
        self.registry.register_model(
            name=name,
            artifact_path=artifact_path,
            metrics={
                "accuracy": val_acc,
                "f1_macro": val_f1,
                "roc_auc_ovr": val_auc,
                "cv_f1_macro": search.best_score_,
                "train_time_sec": train_time,
            },
            params={k: _jsonify(v) for k, v in search.best_params_.items()},
            metadata={
                "search": self.search,
                "cv_folds": self.cv_folds,
                "n_train": int(len(self.X_train)),
                "features": common.ALL_FEATURES,
                "target_encoding": common.RISK_TO_INT,
            },
            selection_metric="f1_macro",
        )
        return result

    @staticmethod
    def _save(estimator, path: str) -> None:
        import joblib

        joblib.dump(estimator, path)
        logger.info("Saved model artifact -> %s", path)

    def train_all(self) -> Dict[str, Dict[str, Any]]:
        """Train and persist every model in the search space."""
        space = self._model_space()
        for name, spec in space.items():
            self.train_one(name, spec)
        self._export_summary()
        self._save_test_split()
        best_name = self.best_model_name()
        logger.info(
            "Best model by validation F1-macro: %s (%.4f)",
            best_name,
            self.results_[best_name]["metrics"]["val_f1_macro"],
        )
        return self.results_

    def best_model_name(self) -> str:
        return max(
            self.results_,
            key=lambda n: self.results_[n]["metrics"]["val_f1_macro"],
        )

    # ------------------------------------------------------------------ #
    # Exports
    # ------------------------------------------------------------------ #
    def _export_summary(self) -> str:
        rows = []
        for name, res in self.results_.items():
            row = {"model": name, **res["metrics"]}
            rows.append(row)
        summary = pd.DataFrame(rows).sort_values(
            "val_f1_macro", ascending=False
        )
        out = os.path.join(common.REPORTS_DIR, "training_summary.csv")
        summary.to_csv(out, index=False)
        logger.info("Training summary written to %s", out)
        return out

    def _save_test_split(self) -> None:
        """Persist the held-out test split so the evaluator uses identical data."""
        test_df = self.X_test.copy()
        test_df[common.TARGET_COL] = self.y_test.map(common.INT_TO_RISK).values
        test_df["_y_int"] = self.y_test.values
        out = os.path.join(common.MODELS_DIR, "test_split.parquet")
        test_df.to_parquet(out, index=False)
        logger.info("Held-out test split saved to %s (%d rows)", out, len(test_df))


def _jsonify(v):
    """Best-effort conversion of numpy scalars to plain Python for JSON."""
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if v is None:
        return None
    return v


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    trainer = ModelTrainer(search="grid")
    trainer.train_all()
    print("\n" + trainer.registry.summary())


if __name__ == "__main__":
    main()
