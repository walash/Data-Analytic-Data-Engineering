"""Descriptive statistics for the CVD risk-stratification feature set.

Provides :class:`DescriptiveAnalyzer`, which computes summary statistics for
numeric features, value counts / proportions for categorical features, the CVD
risk-level target distribution, and a missing-value audit. Results are exported
to ``outputs/reports/``.
"""
from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from .common import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    REPORTS_DIR,
    RISK_ORDER,
    ensure_dirs,
    label_series,
    load_features,
)

logger = logging.getLogger(__name__)


class DescriptiveAnalyzer:
    """Compute and export descriptive statistics for the feature store."""

    def __init__(self, df: pd.DataFrame | None = None):
        ensure_dirs()
        self.df = df if df is not None else load_features()
        self.numeric_cols = [c for c in NUMERIC_FEATURES if c in self.df.columns]
        self.categorical_cols = [
            c for c in CATEGORICAL_FEATURES if c in self.df.columns
        ]

    # ------------------------------------------------------------------ #
    # Numeric summary
    # ------------------------------------------------------------------ #
    def numeric_summary(self) -> pd.DataFrame:
        """Full summary stats incl. skewness and kurtosis for numeric cols."""
        rows = []
        for col in self.numeric_cols:
            s = pd.to_numeric(self.df[col], errors="coerce").dropna()
            if s.empty:
                continue
            rows.append(
                {
                    "feature": col,
                    "count": int(s.count()),
                    "mean": s.mean(),
                    "std": s.std(),
                    "min": s.min(),
                    "q25": s.quantile(0.25),
                    "median": s.median(),
                    "q75": s.quantile(0.75),
                    "max": s.max(),
                    "iqr": s.quantile(0.75) - s.quantile(0.25),
                    "skewness": s.skew(),
                    "kurtosis": s.kurtosis(),
                    "cv": (s.std() / s.mean()) if s.mean() != 0 else np.nan,
                }
            )
        return pd.DataFrame(rows).set_index("feature")

    # ------------------------------------------------------------------ #
    # Categorical summary
    # ------------------------------------------------------------------ #
    def categorical_summary(self) -> pd.DataFrame:
        """Value counts and proportions for every categorical feature."""
        rows = []
        for col in self.categorical_cols:
            labelled = label_series(self.df[col], col)
            counts = labelled.value_counts(dropna=False)
            total = counts.sum()
            for value, cnt in counts.items():
                rows.append(
                    {
                        "feature": col,
                        "category": str(value),
                        "count": int(cnt),
                        "proportion": round(cnt / total, 4) if total else np.nan,
                    }
                )
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # Target distribution
    # ------------------------------------------------------------------ #
    def target_distribution(self) -> pd.DataFrame:
        """Distribution of the CVD risk-level target (Low/Medium/High)."""
        if "risk_level" not in self.df.columns:
            return pd.DataFrame()
        counts = self.df["risk_level"].value_counts()
        counts = counts.reindex(RISK_ORDER).fillna(0).astype(int)
        total = counts.sum()
        out = pd.DataFrame(
            {
                "risk_level": counts.index,
                "count": counts.values,
                "proportion": (counts.values / total).round(4) if total else 0,
            }
        )
        if "target" in self.df.columns:
            dz = self.df.groupby("risk_level", observed=True)["target"].mean()
            out["disease_prevalence"] = (
                out["risk_level"].map(dz).round(4).values
            )
        return out

    # ------------------------------------------------------------------ #
    # Missing value audit
    # ------------------------------------------------------------------ #
    def missing_audit(self) -> pd.DataFrame:
        """Per-column missing count and percentage across the whole frame."""
        n = len(self.df)
        rows = []
        for col in self.df.columns:
            miss = int(self.df[col].isna().sum())
            rows.append(
                {
                    "column": col,
                    "dtype": str(self.df[col].dtype),
                    "missing_count": miss,
                    "missing_pct": round(100 * miss / n, 3) if n else 0.0,
                    "n_unique": int(self.df[col].nunique(dropna=True)),
                }
            )
        return pd.DataFrame(rows).sort_values(
            "missing_pct", ascending=False
        ).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def run(self) -> dict:
        """Compute all tables and export them to ``outputs/reports/``."""
        numeric = self.numeric_summary()
        categorical = self.categorical_summary()
        target = self.target_distribution()
        missing = self.missing_audit()

        numeric_path = os.path.join(REPORTS_DIR, "descriptive_stats.csv")
        cat_path = os.path.join(REPORTS_DIR, "categorical_stats.csv")
        target_path = os.path.join(REPORTS_DIR, "target_distribution.csv")
        missing_path = os.path.join(REPORTS_DIR, "missing_value_audit.csv")

        numeric.round(4).to_csv(numeric_path)
        categorical.to_csv(cat_path, index=False)
        target.to_csv(target_path, index=False)
        missing.to_csv(missing_path, index=False)

        logger.info("Descriptive stats written to %s", REPORTS_DIR)
        return {
            "numeric": numeric,
            "categorical": categorical,
            "target": target,
            "missing": missing,
            "paths": {
                "numeric": numeric_path,
                "categorical": cat_path,
                "target": target_path,
                "missing": missing_path,
            },
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = DescriptiveAnalyzer().run()
    print(res["numeric"].round(3).to_string())
    print("\nTarget distribution:\n", res["target"].to_string(index=False))
