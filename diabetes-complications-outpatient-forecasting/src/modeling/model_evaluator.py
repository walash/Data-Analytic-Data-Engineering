"""Evaluate trained deterioration models and produce comparison artifacts."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.metrics import (accuracy_score, average_precision_score,
                             confusion_matrix, f1_score, precision_recall_curve,
                             precision_score, recall_score, roc_auc_score, roc_curve)

from src.utils.common import get_logger, get_paths

logger = get_logger("model_evaluator")
MODELS = ["logistic_regression", "random_forest", "xgboost", "gradient_boosting"]


class ModelEvaluator:
    def __init__(self):
        self.paths = get_paths()
        split = joblib.load(self.paths["models"] / "test_split.pkl")
        self.X_test, self.y_test = split["X_test"], split["y_test"]
        self.models = {}
        for m in MODELS:
            fp = self.paths["models"] / f"{m}.pkl"
            if fp.exists():
                self.models[m] = joblib.load(fp)

    def _metrics(self):
        rows = []
        self._proba = {}
        for name, model in self.models.items():
            proba = model.predict_proba(self.X_test)[:, 1]
            pred = (proba >= 0.5).astype(int)
            self._proba[name] = proba
            rows.append({
                "model": name,
                "accuracy": accuracy_score(self.y_test, pred),
                "precision": precision_score(self.y_test, pred, zero_division=0),
                "recall": recall_score(self.y_test, pred, zero_division=0),
                "f1": f1_score(self.y_test, pred, zero_division=0),
                "roc_auc": roc_auc_score(self.y_test, proba),
                "pr_auc": average_precision_score(self.y_test, proba),
            })
        df = pd.DataFrame(rows).round(4).sort_values("f1", ascending=False)
        df.to_csv(self.paths["reports"] / "model_evaluation.csv", index=False)
        return df

    def _plot_confusion(self):
        fig, axes = plt.subplots(2, 2, figsize=(13, 11))
        for ax, (name, model) in zip(axes.ravel(), self.models.items()):
            pred = model.predict(self.X_test)
            cm = confusion_matrix(self.y_test, pred)
            sns.heatmap(cm, annot=True, fmt="d", cmap="Blues", ax=ax,
                        xticklabels=["Stable", "Deteriorate"],
                        yticklabels=["Stable", "Deteriorate"])
            ax.set_title(name)
            ax.set_xlabel("Predicted")
            ax.set_ylabel("Actual")
        fig.suptitle("Confusion matrices (test set)", fontsize=16, weight="bold")
        fig.savefig(self.paths["figures"] / "model_confusion_matrices.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _plot_roc(self):
        fig, ax = plt.subplots(figsize=(10, 8))
        for name in self.models:
            fpr, tpr, _ = roc_curve(self.y_test, self._proba[name])
            ax.plot(fpr, tpr, lw=2, label=f"{name} (AUC={roc_auc_score(self.y_test, self._proba[name]):.3f})")
        ax.plot([0, 1], [0, 1], "k--", alpha=0.5)
        ax.set_xlabel("False positive rate")
        ax.set_ylabel("True positive rate")
        ax.set_title("ROC curves — deterioration prediction", fontsize=15, weight="bold")
        ax.legend(loc="lower right")
        fig.savefig(self.paths["figures"] / "model_roc_curves.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _plot_pr(self):
        fig, ax = plt.subplots(figsize=(10, 8))
        for name in self.models:
            prec, rec, _ = precision_recall_curve(self.y_test, self._proba[name])
            ax.plot(rec, prec, lw=2,
                    label=f"{name} (AP={average_precision_score(self.y_test, self._proba[name]):.3f})")
        ax.set_xlabel("Recall")
        ax.set_ylabel("Precision")
        ax.set_title("Precision-Recall curves", fontsize=15, weight="bold")
        ax.legend(loc="upper right")
        fig.savefig(self.paths["figures"] / "model_pr_curves.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _plot_comparison(self, metrics: pd.DataFrame):
        m = metrics.melt(id_vars="model", value_vars=["accuracy", "f1", "roc_auc", "pr_auc"],
                         var_name="metric", value_name="score")
        fig, ax = plt.subplots(figsize=(12, 7))
        sns.barplot(data=m, x="metric", y="score", hue="model", ax=ax)
        ax.set_ylim(0, 1.02)
        ax.set_title("Model comparison", fontsize=15, weight="bold")
        ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left")
        fig.savefig(self.paths["figures"] / "model_comparison.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

    def _plot_feature_importance(self):
        for name in ["random_forest", "xgboost"]:
            if name not in self.models:
                continue
            model = self.models[name]
            pre = model.named_steps["pre"]
            clf = model.named_steps["clf"]
            try:
                feat_names = pre.get_feature_names_out()
            except Exception:
                feat_names = np.array([f"f{i}" for i in range(len(clf.feature_importances_))])
            imp = pd.Series(clf.feature_importances_, index=feat_names).sort_values(ascending=False).head(20)
            imp.to_csv(self.paths["reports"] / f"feature_importance_{name}.csv")
            fig, ax = plt.subplots(figsize=(11, 9))
            sns.barplot(x=imp.values, y=[f.split("__")[-1] for f in imp.index], palette="viridis", ax=ax)
            ax.set_title(f"Top-20 feature importance — {name}", fontsize=14, weight="bold")
            ax.set_xlabel("Importance")
            fig.savefig(self.paths["figures"] / f"feature_importance_{name}.png",
                        dpi=300, bbox_inches="tight")
            plt.close(fig)

    def run(self) -> pd.DataFrame:
        logger.info("=== Model evaluation start ===")
        metrics = self._metrics()
        self._plot_confusion()
        self._plot_roc()
        self._plot_pr()
        self._plot_comparison(metrics)
        self._plot_feature_importance()
        best = metrics.iloc[0]["model"]
        logger.info("Best model by F1: %s (F1=%.4f, AUC=%.4f)",
                    best, metrics.iloc[0]["f1"], metrics.iloc[0]["roc_auc"])
        logger.info("=== Model evaluation done ===")
        return metrics


if __name__ == "__main__":
    ModelEvaluator().run()
