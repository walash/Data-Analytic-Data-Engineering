"""Feature selection & multicollinearity diagnostics."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from sklearn.feature_selection import mutual_info_classif
from statsmodels.stats.outliers_influence import variance_inflation_factor

from src.modeling.common import feature_columns, load_modeling_frame, TARGET
from src.utils.common import get_logger, get_paths

logger = get_logger("feature_selector")


class FeatureSelector:
    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else load_modeling_frame()
        self.paths = get_paths()

    def mutual_information(self) -> pd.DataFrame:
        num, cat, flags = feature_columns(self.df)
        cols = num + flags
        X = self.df[cols].fillna(0)
        y = self.df[TARGET].astype(int)
        mi = mutual_info_classif(X, y, random_state=0)
        out = pd.DataFrame({"feature": cols, "mutual_info": mi}).sort_values(
            "mutual_info", ascending=False).round(4)
        out.to_csv(self.paths["reports"] / "feature_mutual_info.csv", index=False)
        return out

    def vif(self) -> pd.DataFrame:
        num, _, _ = feature_columns(self.df)
        X = self.df[num].fillna(0)
        X = (X - X.mean()) / X.std(ddof=0)
        X = X.assign(_const=1.0)
        rows = []
        for i, col in enumerate(num):
            try:
                v = variance_inflation_factor(X.values, i)
            except Exception:
                v = np.nan
            rows.append({"feature": col, "vif": round(v, 2)})
        out = pd.DataFrame(rows).sort_values("vif", ascending=False)
        out.to_csv(self.paths["reports"] / "feature_vif.csv", index=False)
        return out

    def plot_ranking(self, mi: pd.DataFrame):
        top = mi.head(20)
        fig, ax = plt.subplots(figsize=(11, 9))
        sns.barplot(x="mutual_info", y="feature", data=top, palette="mako", ax=ax)
        ax.set_title("Top-20 features by mutual information with deterioration",
                     fontsize=14, weight="bold")
        fig.savefig(self.paths["figures"] / "feature_selection_mi_ranking.png",
                    dpi=300, bbox_inches="tight")
        plt.close(fig)

    def run(self) -> dict:
        logger.info("=== Feature selection start ===")
        mi = self.mutual_information()
        vif = self.vif()
        self.plot_ranking(mi)
        selected = mi.head(15)["feature"].tolist()
        pd.DataFrame({"selected_feature": selected}).to_csv(
            self.paths["reports"] / "selected_features.csv", index=False)
        high_vif = vif[vif["vif"] > 10]["feature"].tolist()
        logger.info("Top MI feature: %s | high-VIF (>10): %s",
                    mi.iloc[0]["feature"], high_vif or "none")
        logger.info("=== Feature selection done ===")
        return {"mutual_info": mi, "vif": vif, "selected": selected}


if __name__ == "__main__":
    FeatureSelector().run()
