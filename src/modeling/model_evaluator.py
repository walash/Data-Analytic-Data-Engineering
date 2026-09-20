"""Model evaluation & comparison for CVD risk stratification.

Loads the trained pipelines (``models/*.pkl``) and the held-out test split,
then computes a full battery of multi-class evaluation metrics and renders
publication-quality figures to ``outputs/figures/``:

* Per-model classification reports (precision/recall/F1 + macro/weighted)
* Confusion matrices (2x2 grid, one panel per model)
* One-vs-rest ROC curves comparison
* Precision-Recall curves
* Calibration curves
* Feature-importance bar charts (Random Forest + XGBoost, top-20)
* Model-comparison bar chart (accuracy / F1-macro / ROC-AUC)
* Learning curve for the best model

A consolidated evaluation table is written to
``outputs/reports/model_evaluation.csv`` and the best model (by test F1-macro)
is declared.
"""
from __future__ import annotations

import glob
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402

from . import common  # noqa: E402

logger = logging.getLogger(__name__)

sns.set_theme(style="whitegrid")

MODEL_TITLES = {
    "logistic_regression": "Logistic Regression",
    "random_forest": "Random Forest",
    "xgboost": "XGBoost",
    "gradient_boosting": "Gradient Boosting",
}


class ModelEvaluator:
    """Evaluate and compare all trained CVD risk-stratification models."""

    def __init__(self, model_names: Optional[List[str]] = None):
        common.ensure_dirs()
        self.models: Dict[str, Any] = self._load_models(model_names)
        self.X_test, self.y_test = self._load_test_split()
        self.classes = [0, 1, 2]
        self.class_labels = common.RISK_ORDER
        self.eval_rows_: List[Dict[str, Any]] = []
        self.per_class_rows_: List[Dict[str, Any]] = []
        self.best_model_name_: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Loading
    # ------------------------------------------------------------------ #
    def _load_models(self, model_names: Optional[List[str]]) -> Dict[str, Any]:
        import joblib

        models: Dict[str, Any] = {}
        if model_names is None:
            paths = sorted(glob.glob(os.path.join(common.MODELS_DIR, "*.pkl")))
        else:
            paths = [os.path.join(common.MODELS_DIR, f"{n}.pkl") for n in model_names]

        for p in paths:
            name = os.path.splitext(os.path.basename(p))[0]
            if name in ("test_split",):
                continue
            try:
                models[name] = joblib.load(p)
                logger.info("Loaded model %s", name)
            except Exception as exc:  # pragma: no cover
                logger.warning("Could not load %s: %s", p, exc)
        if not models:
            raise FileNotFoundError(
                "No trained models found in models/. Run ModelTrainer first."
            )
        return models

    def _load_test_split(self):
        path = os.path.join(common.MODELS_DIR, "test_split.parquet")
        if not os.path.exists(path):
            raise FileNotFoundError(
                "models/test_split.parquet not found. Run ModelTrainer first."
            )
        df = pd.read_parquet(path)
        y = df["_y_int"].astype(int)
        X = df[common.ALL_FEATURES].copy()
        return X, y

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def evaluate(self) -> pd.DataFrame:
        """Compute the consolidated metrics table across all models."""
        from sklearn.metrics import (
            accuracy_score,
            classification_report,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        from sklearn.preprocessing import label_binarize

        y_true = self.y_test.values
        y_bin = label_binarize(y_true, classes=self.classes)

        for name, model in self.models.items():
            y_pred = model.predict(self.X_test)
            proba = model.predict_proba(self.X_test)

            acc = accuracy_score(y_true, y_pred)
            f1m = f1_score(y_true, y_pred, average="macro")
            f1w = f1_score(y_true, y_pred, average="weighted")
            prec = precision_score(y_true, y_pred, average="macro", zero_division=0)
            rec = recall_score(y_true, y_pred, average="macro", zero_division=0)
            try:
                auc = roc_auc_score(y_bin, proba, average="macro", multi_class="ovr")
            except ValueError:
                auc = float("nan")

            self.eval_rows_.append(
                {
                    "model": name,
                    "accuracy": acc,
                    "precision_macro": prec,
                    "recall_macro": rec,
                    "f1_macro": f1m,
                    "f1_weighted": f1w,
                    "roc_auc_ovr_macro": auc,
                }
            )

            report = classification_report(
                y_true, y_pred, target_names=self.class_labels,
                output_dict=True, zero_division=0,
            )
            for cls in self.class_labels:
                r = report[cls]
                self.per_class_rows_.append(
                    {
                        "model": name,
                        "risk_level": cls,
                        "precision": r["precision"],
                        "recall": r["recall"],
                        "f1_score": r["f1-score"],
                        "support": r["support"],
                    }
                )
            logger.info(
                "%s | acc=%.4f f1_macro=%.4f auc=%.4f", name, acc, f1m, auc
            )

        table = pd.DataFrame(self.eval_rows_).sort_values(
            "f1_macro", ascending=False
        ).reset_index(drop=True)
        self.eval_table_ = table
        self.best_model_name_ = table.iloc[0]["model"]
        return table

    def export_report(self) -> str:
        """Write the consolidated + per-class metric tables to outputs/reports/."""
        if not self.eval_rows_:
            self.evaluate()
        out = os.path.join(common.REPORTS_DIR, "model_evaluation.csv")
        self.eval_table_.to_csv(out, index=False)

        per_class = pd.DataFrame(self.per_class_rows_)
        per_class_out = os.path.join(common.REPORTS_DIR, "model_evaluation_per_class.csv")
        per_class.to_csv(per_class_out, index=False)

        logger.info("Evaluation table written to %s", out)
        return out

    # ------------------------------------------------------------------ #
    # Plots
    # ------------------------------------------------------------------ #
    def plot_confusion_matrices(self) -> str:
        from sklearn.metrics import confusion_matrix

        names = list(self.models.keys())
        n = len(names)
        ncols = 2
        nrows = int(np.ceil(n / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(12, 5 * nrows))
        axes = np.array(axes).reshape(-1)

        for ax, name in zip(axes, names):
            model = self.models[name]
            y_pred = model.predict(self.X_test)
            cm = confusion_matrix(self.y_test, y_pred, labels=self.classes)
            cm_norm = cm.astype(float) / cm.sum(axis=1, keepdims=True)
            sns.heatmap(
                cm, annot=True, fmt="d", cmap="Blues", cbar=False,
                xticklabels=self.class_labels, yticklabels=self.class_labels, ax=ax,
            )
            # Overlay normalized percentages.
            for i in range(cm.shape[0]):
                for j in range(cm.shape[1]):
                    ax.text(
                        j + 0.5, i + 0.72, f"{cm_norm[i, j]*100:.1f}%",
                        ha="center", va="center", fontsize=8, color="gray",
                    )
            ax.set_title(MODEL_TITLES.get(name, name), fontweight="bold")
            ax.set_xlabel("Predicted")
            ax.set_ylabel("Actual")

        for ax in axes[n:]:
            ax.set_visible(False)

        fig.suptitle("Confusion Matrices - CVD Risk Stratification (Test Set)",
                     fontsize=14, fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.97])
        out = os.path.join(common.FIGURES_DIR, "confusion_matrices.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    def plot_roc_curves(self) -> str:
        from sklearn.metrics import auc as auc_fn
        from sklearn.metrics import roc_curve
        from sklearn.preprocessing import label_binarize

        y_bin = label_binarize(self.y_test, classes=self.classes)
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
        palette = sns.color_palette("tab10", len(self.models))

        for ci, cls_idx in enumerate(self.classes):
            ax = axes[ci]
            for mi, (name, model) in enumerate(self.models.items()):
                proba = model.predict_proba(self.X_test)
                fpr, tpr, _ = roc_curve(y_bin[:, ci], proba[:, ci])
                roc_auc = auc_fn(fpr, tpr)
                ax.plot(fpr, tpr, color=palette[mi], lw=1.8,
                        label=f"{MODEL_TITLES.get(name, name)} (AUC={roc_auc:.3f})")
            ax.plot([0, 1], [0, 1], "k--", lw=1)
            ax.set_title(f"ROC (One-vs-Rest): {self.class_labels[ci]} Risk",
                         fontweight="bold")
            ax.set_xlabel("False Positive Rate")
            ax.set_ylabel("True Positive Rate")
            ax.legend(loc="lower right", fontsize=8)

        fig.suptitle("Multi-class ROC Curves (One-vs-Rest)", fontsize=14,
                     fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        out = os.path.join(common.FIGURES_DIR, "roc_curves_comparison.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    def plot_precision_recall(self) -> str:
        from sklearn.metrics import average_precision_score, precision_recall_curve
        from sklearn.preprocessing import label_binarize

        y_bin = label_binarize(self.y_test, classes=self.classes)
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
        palette = sns.color_palette("tab10", len(self.models))

        for ci in range(len(self.classes)):
            ax = axes[ci]
            for mi, (name, model) in enumerate(self.models.items()):
                proba = model.predict_proba(self.X_test)
                prec, rec, _ = precision_recall_curve(y_bin[:, ci], proba[:, ci])
                ap = average_precision_score(y_bin[:, ci], proba[:, ci])
                ax.plot(rec, prec, color=palette[mi], lw=1.8,
                        label=f"{MODEL_TITLES.get(name, name)} (AP={ap:.3f})")
            ax.set_title(f"Precision-Recall: {self.class_labels[ci]} Risk",
                         fontweight="bold")
            ax.set_xlabel("Recall")
            ax.set_ylabel("Precision")
            ax.legend(loc="lower left", fontsize=8)

        fig.suptitle("Precision-Recall Curves (One-vs-Rest)", fontsize=14,
                     fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        out = os.path.join(common.FIGURES_DIR, "precision_recall_curves.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    def plot_calibration(self) -> str:
        from sklearn.calibration import calibration_curve
        from sklearn.preprocessing import label_binarize

        y_bin = label_binarize(self.y_test, classes=self.classes)
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
        palette = sns.color_palette("tab10", len(self.models))

        for ci in range(len(self.classes)):
            ax = axes[ci]
            for mi, (name, model) in enumerate(self.models.items()):
                proba = model.predict_proba(self.X_test)[:, ci]
                try:
                    frac_pos, mean_pred = calibration_curve(
                        y_bin[:, ci], proba, n_bins=10, strategy="quantile"
                    )
                    ax.plot(mean_pred, frac_pos, "o-", color=palette[mi], lw=1.5,
                            markersize=4,
                            label=MODEL_TITLES.get(name, name))
                except Exception:  # pragma: no cover
                    continue
            ax.plot([0, 1], [0, 1], "k--", lw=1, label="Perfectly calibrated")
            ax.set_title(f"Calibration: {self.class_labels[ci]} Risk",
                         fontweight="bold")
            ax.set_xlabel("Mean predicted probability")
            ax.set_ylabel("Fraction of positives")
            ax.legend(loc="upper left", fontsize=8)

        fig.suptitle("Calibration Curves (One-vs-Rest)", fontsize=14,
                     fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.95])
        out = os.path.join(common.FIGURES_DIR, "calibration_curves.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    def _extract_feature_importance(self, name: str) -> Optional[pd.DataFrame]:
        """Return a (feature, importance) frame for a tree-based pipeline."""
        model = self.models.get(name)
        if model is None:
            return None
        try:
            pre = model.named_steps["preprocessor"]
            clf = model.named_steps["classifier"]
        except (AttributeError, KeyError):
            return None
        if not hasattr(clf, "feature_importances_"):
            return None
        names = common.get_feature_names_out(pre)
        importances = clf.feature_importances_
        if len(names) != len(importances):
            names = [f"f{i}" for i in range(len(importances))]
        return (
            pd.DataFrame({"feature": names, "importance": importances})
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )

    def plot_feature_importance(self, top_n: int = 20) -> Optional[str]:
        tree_models = [n for n in ("random_forest", "xgboost") if n in self.models]
        if not tree_models:
            return None
        fig, axes = plt.subplots(1, len(tree_models),
                                 figsize=(9 * len(tree_models), 8))
        axes = np.atleast_1d(axes)

        exported = {}
        for ax, name in zip(axes, tree_models):
            imp = self._extract_feature_importance(name)
            if imp is None:
                continue
            exported[name] = imp
            top = imp.head(top_n).iloc[::-1]
            sns.barplot(data=top, y="feature", x="importance", hue="feature",
                        palette="mako", legend=False, ax=ax)
            ax.set_title(f"{MODEL_TITLES.get(name, name)} - Top {top_n} Features",
                         fontweight="bold")
            ax.set_xlabel("Importance (impurity/gain)")
            ax.set_ylabel("")

        fig.suptitle("Feature Importance - Tree Ensembles", fontsize=14,
                     fontweight="bold")
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        out = os.path.join(common.FIGURES_DIR, "feature_importance.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)

        # Export the underlying tables too.
        for name, imp in exported.items():
            imp.to_csv(
                os.path.join(common.REPORTS_DIR, f"feature_importance_{name}.csv"),
                index=False,
            )
        logger.info("Saved %s", out)
        return out

    def plot_model_comparison(self) -> str:
        if not self.eval_rows_:
            self.evaluate()
        metrics = ["accuracy", "f1_macro", "roc_auc_ovr_macro"]
        melt = self.eval_table_.melt(
            id_vars="model", value_vars=metrics,
            var_name="metric", value_name="score",
        )
        melt["model"] = melt["model"].map(lambda n: MODEL_TITLES.get(n, n))

        fig, ax = plt.subplots(figsize=(11, 6))
        sns.barplot(data=melt, x="model", y="score", hue="metric",
                    palette="Set2", ax=ax)
        ax.set_title("Model Comparison - Test-set Performance", fontweight="bold")
        ax.set_ylim(0, 1.02)
        ax.set_xlabel("")
        ax.set_ylabel("Score")
        ax.legend(title="Metric", loc="lower right")
        for container in ax.containers:
            ax.bar_label(container, fmt="%.3f", fontsize=7, padding=2)
        fig.tight_layout()
        out = os.path.join(common.FIGURES_DIR, "model_comparison.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    def plot_learning_curve(self, name: Optional[str] = None) -> str:
        from sklearn.model_selection import learning_curve

        if self.best_model_name_ is None:
            self.evaluate()
        name = name or self.best_model_name_
        model = self.models[name]

        # Combine train+test features from the full frame for the learning curve.
        df = common.load_feature_frame()
        X, y = common.get_X_y(df)

        train_sizes, train_scores, val_scores = learning_curve(
            model, X, y, cv=5, scoring="f1_macro",
            train_sizes=np.linspace(0.1, 1.0, 6),
            n_jobs=-1, random_state=common.RANDOM_STATE, shuffle=True,
        )
        train_mean = train_scores.mean(axis=1)
        train_std = train_scores.std(axis=1)
        val_mean = val_scores.mean(axis=1)
        val_std = val_scores.std(axis=1)

        fig, ax = plt.subplots(figsize=(9, 6))
        ax.plot(train_sizes, train_mean, "o-", color="#1f77b4", label="Training F1-macro")
        ax.fill_between(train_sizes, train_mean - train_std, train_mean + train_std,
                        alpha=0.15, color="#1f77b4")
        ax.plot(train_sizes, val_mean, "o-", color="#d62728",
                label="Cross-validation F1-macro")
        ax.fill_between(train_sizes, val_mean - val_std, val_mean + val_std,
                        alpha=0.15, color="#d62728")
        ax.set_title(f"Learning Curve - {MODEL_TITLES.get(name, name)}",
                     fontweight="bold")
        ax.set_xlabel("Training examples")
        ax.set_ylabel("F1-macro")
        ax.legend(loc="lower right")
        fig.tight_layout()
        out = os.path.join(common.FIGURES_DIR, "learning_curve_best_model.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved %s", out)
        return out

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def run(self) -> Dict[str, Any]:
        self.evaluate()
        self.export_report()
        self.plot_confusion_matrices()
        self.plot_roc_curves()
        self.plot_precision_recall()
        self.plot_calibration()
        self.plot_feature_importance()
        self.plot_model_comparison()
        self.plot_learning_curve()
        logger.info(
            "BEST MODEL (by test F1-macro): %s (%.4f)",
            self.best_model_name_,
            float(self.eval_table_.iloc[0]["f1_macro"]),
        )
        return {
            "best_model": self.best_model_name_,
            "evaluation_table": self.eval_table_,
        }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    evaluator = ModelEvaluator()
    out = evaluator.run()
    print("\nEvaluation table:")
    print(out["evaluation_table"].to_string(index=False))
    print(f"\nBest model: {out['best_model']}")


if __name__ == "__main__":
    main()
