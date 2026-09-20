"""Feature selection utilities for CVD risk stratification.

Implements three complementary techniques on the model-ready feature matrix:

* **Recursive Feature Elimination with cross-validation (RFECV)** - ranks
  features by wrapping a Random Forest and eliminating the weakest recursively.
* **Mutual Information** - model-free dependency between each feature and the
  risk-level target.
* **Variance Inflation Factor (VIF)** - flags multicollinearity among the
  numeric predictors.

Selected features and rankings are exported to ``outputs/reports/`` and a
feature-importance ranking figure is written to ``outputs/figures/``.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import numpy as np
import pandas as pd

from . import common

logger = logging.getLogger(__name__)

matplotlib = None  # lazy import guard


class FeatureSelector:
    """Run RFE, mutual information, and VIF analysis over the feature store."""

    def __init__(self, df: Optional[pd.DataFrame] = None, top_n: int = 20):
        common.ensure_dirs()
        self.df = df if df is not None else common.load_feature_frame()
        self.top_n = top_n
        self.X, self.y = common.get_X_y(self.df)

        # Fit a preprocessor once so every method works on the expanded matrix.
        self.preprocessor = common.build_preprocessor()
        self.X_enc = self.preprocessor.fit_transform(self.X, self.y)
        self.feature_names = common.get_feature_names_out(self.preprocessor)

        self.mi_scores_: Optional[pd.DataFrame] = None
        self.rfe_ranking_: Optional[pd.DataFrame] = None
        self.vif_: Optional[pd.DataFrame] = None
        self.selected_features_: List[str] = []

    # ------------------------------------------------------------------ #
    # Mutual information
    # ------------------------------------------------------------------ #
    def mutual_information(self) -> pd.DataFrame:
        """Compute mutual information between each expanded feature and target."""
        from sklearn.feature_selection import mutual_info_classif

        logger.info("Computing mutual information scores ...")
        scores = mutual_info_classif(
            self.X_enc, self.y, random_state=common.RANDOM_STATE
        )
        mi = (
            pd.DataFrame(
                {"feature": self.feature_names, "mutual_information": scores}
            )
            .sort_values("mutual_information", ascending=False)
            .reset_index(drop=True)
        )
        self.mi_scores_ = mi
        return mi

    # ------------------------------------------------------------------ #
    # Recursive Feature Elimination (with CV)
    # ------------------------------------------------------------------ #
    def recursive_elimination(self, n_features: Optional[int] = None) -> pd.DataFrame:
        """Rank features via RFECV wrapping a balanced Random Forest."""
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.feature_selection import RFECV
        from sklearn.model_selection import StratifiedKFold

        logger.info("Running RFECV (this may take a moment) ...")
        estimator = RandomForestClassifier(
            n_estimators=200,
            max_depth=None,
            class_weight="balanced",
            random_state=common.RANDOM_STATE,
            n_jobs=-1,
        )
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=common.RANDOM_STATE)
        selector = RFECV(
            estimator=estimator,
            step=1,
            min_features_to_select=min(self.top_n, len(self.feature_names)),
            cv=cv,
            scoring="f1_macro",
            n_jobs=-1,
        )
        selector.fit(self.X_enc, self.y)

        ranking = (
            pd.DataFrame(
                {
                    "feature": self.feature_names,
                    "rfe_ranking": selector.ranking_,
                    "rfe_selected": selector.support_,
                }
            )
            .sort_values(["rfe_ranking", "feature"])
            .reset_index(drop=True)
        )
        self.rfe_ranking_ = ranking
        logger.info(
            "RFECV selected %d / %d features (optimal).",
            int(selector.n_features_),
            len(self.feature_names),
        )
        return ranking

    # ------------------------------------------------------------------ #
    # Variance Inflation Factor
    # ------------------------------------------------------------------ #
    def variance_inflation(self) -> pd.DataFrame:
        """Compute VIF for the numeric predictors to flag multicollinearity."""
        from statsmodels.stats.outliers_influence import variance_inflation_factor
        from sklearn.preprocessing import StandardScaler

        logger.info("Computing Variance Inflation Factors ...")
        numeric = common.NUMERIC_FEATURES
        Xn = self.X[numeric].astype(float).fillna(0.0)
        Xn = pd.DataFrame(
            StandardScaler().fit_transform(Xn), columns=numeric
        )
        Xn = Xn.assign(_const=1.0)  # intercept term for a well-posed VIF

        rows = []
        for i, col in enumerate(numeric):
            try:
                vif = variance_inflation_factor(Xn.values, i)
            except Exception:  # pragma: no cover
                vif = np.nan
            rows.append({"feature": col, "vif": vif})

        vif_df = (
            pd.DataFrame(rows)
            .sort_values("vif", ascending=False)
            .reset_index(drop=True)
        )
        vif_df["multicollinear_flag"] = vif_df["vif"] > 5.0
        self.vif_ = vif_df
        return vif_df

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def select(self) -> List[str]:
        """Combine MI + RFE rankings and return the top-N selected features."""
        if self.mi_scores_ is None:
            self.mutual_information()
        if self.rfe_ranking_ is None:
            self.recursive_elimination()

        merged = self.mi_scores_.merge(self.rfe_ranking_, on="feature", how="outer")

        # Composite score: normalized MI (higher better) + inverse RFE rank.
        mi = merged["mutual_information"].fillna(0.0)
        mi_norm = (mi - mi.min()) / (mi.max() - mi.min() + 1e-12)
        rank = merged["rfe_ranking"].fillna(merged["rfe_ranking"].max())
        rank_norm = 1.0 - (rank - rank.min()) / (rank.max() - rank.min() + 1e-12)
        merged["composite_score"] = 0.5 * mi_norm + 0.5 * rank_norm
        merged = merged.sort_values("composite_score", ascending=False).reset_index(
            drop=True
        )

        self.ranking_table_ = merged
        self.selected_features_ = merged["feature"].head(self.top_n).tolist()
        return self.selected_features_

    def export(self) -> str:
        """Write selected features + full ranking table to outputs/reports/."""
        if not self.selected_features_:
            self.select()
        if self.vif_ is None:
            self.variance_inflation()

        out_path = os.path.join(common.REPORTS_DIR, "selected_features.csv")
        self.ranking_table_.to_csv(out_path, index=False)

        vif_path = os.path.join(common.REPORTS_DIR, "feature_vif.csv")
        self.vif_.to_csv(vif_path, index=False)

        logger.info("Selected features written to %s", out_path)
        logger.info("VIF table written to %s", vif_path)
        return out_path

    def plot_ranking(self, top_n: Optional[int] = None) -> str:
        """Render a horizontal bar chart of the top-N features by composite score."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns

        if not self.selected_features_:
            self.select()
        top_n = top_n or self.top_n
        data = self.ranking_table_.head(top_n).iloc[::-1]

        sns.set_theme(style="whitegrid")
        fig, ax = plt.subplots(figsize=(10, 8))
        sns.barplot(
            data=data,
            y="feature",
            x="composite_score",
            hue="feature",
            palette="viridis",
            legend=False,
            ax=ax,
        )
        ax.set_title(
            f"Top {top_n} Features by Composite Selection Score\n"
            "(0.5 * normalized mutual information + 0.5 * inverse RFE rank)",
            fontsize=12, fontweight="bold",
        )
        ax.set_xlabel("Composite selection score")
        ax.set_ylabel("Feature")
        fig.tight_layout()

        out = os.path.join(common.FIGURES_DIR, "feature_selection_ranking.png")
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        logger.info("Feature-selection ranking figure saved to %s", out)
        return out

    def run(self) -> List[str]:
        """Full feature-selection pipeline: MI + RFE + VIF + export + figure."""
        self.mutual_information()
        self.recursive_elimination()
        self.variance_inflation()
        self.select()
        self.export()
        self.plot_ranking()
        return self.selected_features_


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    selector = FeatureSelector()
    selected = selector.run()
    print("\nTop selected features:")
    for i, f in enumerate(selected, 1):
        print(f"  {i:2d}. {f}")


if __name__ == "__main__":
    main()
