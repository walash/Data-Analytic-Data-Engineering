"""Inferential statistics linking features to complications / deterioration."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from src.eda.common import CATEGORICAL_FEATURES, FLAG_FEATURES, NUMERIC_FEATURES, load_features
from src.utils.common import get_logger, get_paths

logger = get_logger("statistical_tests")


class StatisticalTester:
    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else load_features()
        self.reports = get_paths()["reports"]

    @staticmethod
    def _cramers_v(confusion: np.ndarray) -> float:
        chi2 = stats.chi2_contingency(confusion)[0]
        n = confusion.sum()
        r, k = confusion.shape
        return float(np.sqrt(chi2 / (n * (min(r, k) - 1)))) if min(r, k) > 1 else np.nan

    def chi_square_tests(self) -> pd.DataFrame:
        rows = []
        for c in CATEGORICAL_FEATURES + FLAG_FEATURES:
            if c not in self.df:
                continue
            ct = pd.crosstab(self.df[c], self.df["complication"])
            if ct.shape[0] < 2:
                continue
            chi2, p, dof, _ = stats.chi2_contingency(ct)
            rows.append({"feature": c, "test": "chi_square_vs_complication",
                         "statistic": round(chi2, 3), "p_value": p, "dof": dof,
                         "effect_size_cramers_v": round(self._cramers_v(ct.values), 3),
                         "significant": p < 0.05})
        return pd.DataFrame(rows)

    def anova_tests(self) -> pd.DataFrame:
        rows = []
        groups_key = "complication"
        for c in NUMERIC_FEATURES:
            if c not in self.df:
                continue
            groups = [g[c].dropna().values for _, g in self.df.groupby(groups_key)]
            if len(groups) < 2:
                continue
            f, p = stats.f_oneway(*groups)
            h, ph = stats.kruskal(*groups)
            rows.append({"feature": c, "anova_F": round(f, 3), "anova_p": p,
                         "kruskal_H": round(h, 3), "kruskal_p": ph,
                         "significant": p < 0.05})
        return pd.DataFrame(rows)

    def deterioration_associations(self) -> pd.DataFrame:
        """Point-biserial correlation of numeric features with deterioration flag."""
        rows = []
        y = self.df["deteriorated_12m"].astype(float)
        for c in NUMERIC_FEATURES:
            if c not in self.df:
                continue
            r, p = stats.pointbiserialr(y, self.df[c].astype(float))
            rows.append({"feature": c, "point_biserial_r": round(r, 3),
                         "p_value": p, "significant": p < 0.05})
        out = pd.DataFrame(rows).sort_values("point_biserial_r", key=abs, ascending=False)
        return out

    def normality_tests(self) -> pd.DataFrame:
        rows = []
        for c in ["age", "bmi", "hba1c", "egfr", "systolic_bp"]:
            if c not in self.df:
                continue
            sample = self.df[c].dropna().sample(min(4000, len(self.df)), random_state=0)
            w, p = stats.shapiro(sample)
            rows.append({"feature": c, "shapiro_W": round(w, 4), "p_value": p,
                         "normal": p > 0.05})
        return pd.DataFrame(rows)

    def run_all(self) -> pd.DataFrame:
        logger.info("Running statistical tests")
        chi = self.chi_square_tests()
        anova = self.anova_tests()
        pb = self.deterioration_associations()
        norm = self.normality_tests()

        chi.to_csv(self.reports / "stat_chi_square.csv", index=False)
        anova.to_csv(self.reports / "stat_anova.csv", index=False)
        pb.to_csv(self.reports / "stat_deterioration_assoc.csv", index=False)
        norm.to_csv(self.reports / "stat_normality.csv", index=False)

        combined = pd.concat([
            chi.assign(group="chi_square")[["group", "feature", "p_value", "significant"]],
            anova.assign(group="anova").rename(columns={"anova_p": "p_value"})[
                ["group", "feature", "p_value", "significant"]],
            pb.assign(group="deterioration_pointbiserial")[
                ["group", "feature", "p_value", "significant"]],
        ], ignore_index=True)
        combined.to_csv(self.reports / "statistical_tests_summary.csv", index=False)
        logger.info("Statistical tests complete: %d rows in summary", len(combined))
        return combined


if __name__ == "__main__":
    StatisticalTester().run_all()
