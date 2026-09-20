"""Statistical hypothesis testing for CVD risk associations.

:class:`StatisticalTester` runs:

* Chi-square tests of independence (categorical features vs CVD risk level)
* One-way ANOVA and Kruskal-Wallis (numeric features across risk groups)
* Point-biserial correlation (each numeric feature vs binary disease target)
* Shapiro-Wilk normality tests for key numeric features

All results are consolidated to ``outputs/reports/statistical_tests.csv`` with
test statistics, p-values, and significance flags.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd
from scipy import stats

from .common import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    REPORTS_DIR,
    RISK_ORDER,
    ensure_dirs,
    load_features,
)

logger = logging.getLogger(__name__)

ALPHA = 0.05


def _sig_flag(p: float) -> str:
    if p is None or np.isnan(p):
        return "n/a"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


def _cramers_v(confusion: np.ndarray) -> float:
    """Bias-corrected Cramer's V effect size for a contingency table."""
    chi2 = stats.chi2_contingency(confusion)[0]
    n = confusion.sum()
    if n == 0:
        return np.nan
    phi2 = chi2 / n
    r, k = confusion.shape
    phi2corr = max(0, phi2 - (k - 1) * (r - 1) / (n - 1))
    rcorr = r - (r - 1) ** 2 / (n - 1)
    kcorr = k - (k - 1) ** 2 / (n - 1)
    denom = min(kcorr - 1, rcorr - 1)
    return np.sqrt(phi2corr / denom) if denom > 0 else np.nan


class StatisticalTester:
    """Run the full statistical test battery and export results."""

    def __init__(self, df: pd.DataFrame | None = None):
        ensure_dirs()
        self.df = df if df is not None else load_features()
        self.numeric_cols = [c for c in NUMERIC_FEATURES if c in self.df.columns]
        self.categorical_cols = [
            c for c in CATEGORICAL_FEATURES
            if c in self.df.columns and c not in ("target",)
        ]

    # ------------------------------------------------------------------ #
    # Chi-square: categorical vs risk level
    # ------------------------------------------------------------------ #
    def chi_square_tests(self) -> pd.DataFrame:
        rows = []
        risk = self.df["risk_level"].astype(str)
        for col in self.categorical_cols:
            ct = pd.crosstab(self.df[col], risk)
            if ct.shape[0] < 2 or ct.shape[1] < 2:
                continue
            chi2, p, dof, _ = stats.chi2_contingency(ct)
            rows.append(
                {
                    "test": "chi_square",
                    "feature": col,
                    "target": "risk_level",
                    "statistic": round(chi2, 4),
                    "dof": int(dof),
                    "p_value": p,
                    "effect_size": round(_cramers_v(ct.values), 4),
                    "effect_metric": "cramers_v",
                    "significant": p < ALPHA,
                    "significance": _sig_flag(p),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # ANOVA + Kruskal-Wallis: numeric across risk groups
    # ------------------------------------------------------------------ #
    def anova_kruskal_tests(self) -> pd.DataFrame:
        rows = []
        groups_idx = {
            r: self.df[self.df["risk_level"] == r] for r in RISK_ORDER
        }
        for col in self.numeric_cols:
            samples = [
                pd.to_numeric(g[col], errors="coerce").dropna().values
                for g in groups_idx.values()
            ]
            samples = [s for s in samples if len(s) > 1]
            if len(samples) < 2:
                continue
            f_stat, p_anova = stats.f_oneway(*samples)
            h_stat, p_kw = stats.kruskal(*samples)
            # eta-squared effect size from one-way ANOVA
            grand = np.concatenate(samples)
            ss_total = ((grand - grand.mean()) ** 2).sum()
            ss_between = sum(
                len(s) * (s.mean() - grand.mean()) ** 2 for s in samples
            )
            eta_sq = ss_between / ss_total if ss_total > 0 else np.nan
            rows.append(
                {
                    "test": "anova",
                    "feature": col,
                    "target": "risk_level",
                    "statistic": round(f_stat, 4),
                    "dof": len(samples) - 1,
                    "p_value": p_anova,
                    "effect_size": round(eta_sq, 4),
                    "effect_metric": "eta_squared",
                    "significant": p_anova < ALPHA,
                    "significance": _sig_flag(p_anova),
                }
            )
            rows.append(
                {
                    "test": "kruskal_wallis",
                    "feature": col,
                    "target": "risk_level",
                    "statistic": round(h_stat, 4),
                    "dof": len(samples) - 1,
                    "p_value": p_kw,
                    "effect_size": np.nan,
                    "effect_metric": "",
                    "significant": p_kw < ALPHA,
                    "significance": _sig_flag(p_kw),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # Point-biserial correlation vs binary disease target
    # ------------------------------------------------------------------ #
    def point_biserial_tests(self) -> pd.DataFrame:
        rows = []
        if "target" not in self.df.columns:
            return pd.DataFrame(rows)
        target = pd.to_numeric(self.df["target"], errors="coerce")
        for col in self.numeric_cols:
            x = pd.to_numeric(self.df[col], errors="coerce")
            valid = x.notna() & target.notna()
            if valid.sum() < 3 or target[valid].nunique() < 2:
                continue
            r, p = stats.pointbiserialr(target[valid], x[valid])
            rows.append(
                {
                    "test": "point_biserial",
                    "feature": col,
                    "target": "target(disease)",
                    "statistic": round(r, 4),
                    "dof": int(valid.sum() - 2),
                    "p_value": p,
                    "effect_size": round(abs(r), 4),
                    "effect_metric": "|r|",
                    "significant": p < ALPHA,
                    "significance": _sig_flag(p),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # Shapiro-Wilk normality
    # ------------------------------------------------------------------ #
    def normality_tests(self, max_n: int = 5000) -> pd.DataFrame:
        rows = []
        key = ["age", "resting_bp", "cholesterol", "max_heart_rate",
               "st_depression", "framingham_risk_score"]
        for col in [c for c in key if c in self.df.columns]:
            s = pd.to_numeric(self.df[col], errors="coerce").dropna()
            if len(s) < 3:
                continue
            if len(s) > max_n:
                s = s.sample(max_n, random_state=42)
            w, p = stats.shapiro(s)
            rows.append(
                {
                    "test": "shapiro_wilk",
                    "feature": col,
                    "target": "normality",
                    "statistic": round(w, 4),
                    "dof": len(s),
                    "p_value": p,
                    "effect_size": np.nan,
                    "effect_metric": "",
                    "significant": p < ALPHA,
                    "significance": _sig_flag(p),
                    "interpretation": ("non-normal" if p < ALPHA else "normal"),
                }
            )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # orchestration
    # ------------------------------------------------------------------ #
    def run(self) -> pd.DataFrame:
        frames = [
            self.chi_square_tests(),
            self.anova_kruskal_tests(),
            self.point_biserial_tests(),
            self.normality_tests(),
        ]
        combined = pd.concat([f for f in frames if not f.empty],
                             ignore_index=True)
        # order p-value column formatting for readability
        combined["p_value"] = combined["p_value"].astype(float)
        out_path = os.path.join(REPORTS_DIR, "statistical_tests.csv")
        combined.to_csv(out_path, index=False)
        logger.info("Statistical test results written to %s", out_path)
        return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = StatisticalTester().run()
    with pd.option_context("display.max_rows", None, "display.width", 160):
        print(res.to_string(index=False))
