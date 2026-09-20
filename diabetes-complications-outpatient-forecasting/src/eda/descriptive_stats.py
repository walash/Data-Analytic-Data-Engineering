"""Descriptive statistics for the T2D feature store."""
from __future__ import annotations

import pandas as pd
from scipy import stats

from src.eda.common import CATEGORICAL_FEATURES, NUMERIC_FEATURES, load_features
from src.utils.common import get_logger, get_paths

logger = get_logger("descriptive_stats")


class DescriptiveAnalyzer:
    def __init__(self, df: pd.DataFrame | None = None):
        self.df = df if df is not None else load_features()
        self.reports = get_paths()["reports"]

    def numeric_summary(self) -> pd.DataFrame:
        rows = []
        for c in NUMERIC_FEATURES:
            if c not in self.df:
                continue
            s = self.df[c].dropna()
            rows.append({
                "feature": c, "count": s.count(), "mean": s.mean(), "std": s.std(),
                "min": s.min(), "q25": s.quantile(0.25), "median": s.median(),
                "q75": s.quantile(0.75), "max": s.max(),
                "skew": stats.skew(s), "kurtosis": stats.kurtosis(s),
            })
        out = pd.DataFrame(rows).round(3)
        out.to_csv(self.reports / "descriptive_numeric.csv", index=False)
        return out

    def categorical_summary(self) -> pd.DataFrame:
        rows = []
        for c in CATEGORICAL_FEATURES:
            if c not in self.df:
                continue
            vc = self.df[c].value_counts(dropna=False)
            prop = self.df[c].value_counts(normalize=True, dropna=False)
            for level in vc.index:
                rows.append({"feature": c, "level": str(level),
                             "count": int(vc[level]), "proportion": round(float(prop[level]), 4)})
        out = pd.DataFrame(rows)
        out.to_csv(self.reports / "descriptive_categorical.csv", index=False)
        return out

    def target_summary(self) -> pd.DataFrame:
        comp = self.df["complication"].value_counts(normalize=True).round(4)
        det = self.df["deteriorated_12m"].value_counts(normalize=True).round(4)
        out = pd.DataFrame({
            "target": (["complication:" + str(i) for i in comp.index]
                       + ["deteriorated_12m:" + str(i) for i in det.index]),
            "proportion": list(comp.values) + list(det.values),
        })
        out.to_csv(self.reports / "target_distribution.csv", index=False)
        return out

    def missing_audit(self) -> pd.DataFrame:
        miss = self.df.isna().mean().sort_values(ascending=False)
        out = pd.DataFrame({"feature": miss.index, "missing_rate": miss.values.round(4)})
        out.to_csv(self.reports / "missing_audit.csv", index=False)
        return out

    def run_all(self) -> dict:
        logger.info("Computing descriptive statistics")
        return {
            "numeric": self.numeric_summary(),
            "categorical": self.categorical_summary(),
            "target": self.target_summary(),
            "missing": self.missing_audit(),
        }


if __name__ == "__main__":
    DescriptiveAnalyzer().run_all()
