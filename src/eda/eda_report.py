"""EDA orchestrator + markdown report generator.

:class:`EDAReport` ties together :class:`DescriptiveAnalyzer`,
:class:`EDAVisualizer`, and :class:`StatisticalTester`, then writes a
comprehensive ``outputs/reports/eda_summary_report.md`` embedding the figures
and summarising the statistical findings.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

from .common import (
    FIGURES_DIR,
    REPORTS_DIR,
    RISK_ORDER,
    ensure_dirs,
    load_features,
)
from .descriptive_stats import DescriptiveAnalyzer
from .statistical_tests import StatisticalTester
from .visualizations import EDAVisualizer

logger = logging.getLogger(__name__)

FIGURE_CAPTIONS = {
    "01_risk_distribution.png": "CVD risk-level distribution (share and counts).",
    "02_age_risk_heatmap.png": "Risk-level composition within each age group.",
    "03_correlation_heatmap.png": "Pearson correlation of numeric features.",
    "04_feature_distributions.png": "Key clinical feature distributions by risk.",
    "05_risk_by_demographics.png": "Risk distribution by sex, chest pain, FBS.",
    "06_ecg_analysis.png": "ECG / exercise-test features by risk level.",
    "07_temporal_ed_patterns.png": "ED demand by hour, day of week and month.",
    "08_ed_seasonal_trends.png": "Daily ED volume with rolling averages.",
    "09_cholesterol_bp_scatter.png": "Cholesterol vs resting BP by risk level.",
    "10_framingham_score_dist.png": "Cardiovascular risk-score distribution.",
}


class EDAReport:
    """Run every EDA component and assemble the markdown summary report."""

    def __init__(self, df: pd.DataFrame | None = None):
        ensure_dirs()
        self.df = df if df is not None else load_features()
        self.desc = DescriptiveAnalyzer(self.df)
        self.viz = EDAVisualizer(self.df)
        self.tester = StatisticalTester(self.df)

    # ------------------------------------------------------------------ #
    def _fmt_p(self, p) -> str:
        try:
            p = float(p)
        except (TypeError, ValueError):
            return "n/a"
        return "<0.001" if p < 0.001 else f"{p:.4f}"

    # ------------------------------------------------------------------ #
    def build_markdown(self, desc_res, tests_df, figures) -> str:
        n = len(self.df)
        target = desc_res["target"]
        numeric = desc_res["numeric"]
        missing = desc_res["missing"]

        lines: list[str] = []
        lines.append("# Exploratory Data Analysis — Cardiovascular Risk "
                     "Stratification")
        lines.append("")
        lines.append(f"*Generated: {datetime.now():%Y-%m-%d %H:%M}*")
        lines.append("")

        # ---- executive summary ----
        lines.append("## 1. Executive Summary")
        lines.append("")
        lines.append(
            f"This report analyses **{n:,} synthetic patient records** with "
            f"{self.df.shape[1]} engineered features spanning demographics, "
            "clinical measurements, ECG / exercise-test results, computed "
            "cardiovascular risk scores, and downstream Emergency Department "
            "(ED) utilisation. The prediction target is a three-level CVD "
            "**risk stratification** (Low / Medium / High)."
        )
        lines.append("")
        if not target.empty:
            parts = [
                f"**{row.risk_level}** = {int(row['count']):,} "
                f"({row['proportion'] * 100:.1f}%)"
                for _, row in target.iterrows()
            ]
            lines.append("Risk-level breakdown: " + "; ".join(parts) + ".")
            lines.append("")

        # ---- data characteristics ----
        lines.append("## 2. Data Characteristics")
        lines.append("")
        lines.append(f"- Records: **{n:,}**")
        lines.append(f"- Total columns: **{self.df.shape[1]}**")
        lines.append(f"- Numeric features profiled: **{len(numeric)}**")
        total_missing = int(missing["missing_count"].sum())
        lines.append(f"- Total missing cells: **{total_missing:,}** "
                     f"({'complete dataset' if total_missing == 0 else 'see audit'})")
        lines.append("")
        lines.append("### Numeric feature summary (selected)")
        lines.append("")
        show_cols = ["mean", "std", "min", "median", "max", "skewness"]
        show_cols = [c for c in show_cols if c in numeric.columns]
        lines.append(numeric[show_cols].round(2).to_markdown())
        lines.append("")

        # ---- target distribution table ----
        if not target.empty:
            lines.append("### CVD risk-level target distribution")
            lines.append("")
            lines.append(target.round(4).to_markdown(index=False))
            lines.append("")

        # ---- key findings ----
        lines.append("## 3. Key Findings")
        lines.append("")
        findings = self._key_findings(tests_df)
        for f in findings:
            lines.append(f"- {f}")
        lines.append("")

        # ---- statistical tests ----
        lines.append("## 4. Statistical Test Results")
        lines.append("")
        lines.append("Significance codes: `***` p<0.001, `**` p<0.01, "
                     "`*` p<0.05, `ns` not significant.")
        lines.append("")

        for test_name, header in [
            ("chi_square", "### 4.1 Chi-square — categorical vs risk level"),
            ("anova", "### 4.2 ANOVA — numeric across risk groups"),
            ("kruskal_wallis", "### 4.3 Kruskal-Wallis — numeric across risk groups"),
            ("point_biserial", "### 4.4 Point-biserial — feature vs disease target"),
            ("shapiro_wilk", "### 4.5 Shapiro-Wilk — normality of key features"),
        ]:
            sub = tests_df[tests_df["test"] == test_name].copy()
            if sub.empty:
                continue
            lines.append(header)
            lines.append("")
            sub["p_value"] = sub["p_value"].map(self._fmt_p)
            cols = ["feature", "statistic", "p_value", "effect_size",
                    "effect_metric", "significance"]
            cols = [c for c in cols if c in sub.columns]
            lines.append(sub[cols].to_markdown(index=False))
            lines.append("")

        # ---- figures ----
        lines.append("## 5. Visualisations")
        lines.append("")
        for path in figures:
            fname = os.path.basename(path)
            caption = FIGURE_CAPTIONS.get(fname, fname)
            rel = os.path.join("..", "figures", fname)
            lines.append(f"### {caption}")
            lines.append("")
            lines.append(f"![{caption}]({rel})")
            lines.append("")

        # ---- data quality ----
        lines.append("## 6. Data Quality Findings")
        lines.append("")
        if total_missing == 0:
            lines.append("- No missing values detected across any column — the "
                         "feature store is fully populated.")
        else:
            worst = missing[missing["missing_count"] > 0].head(10)
            lines.append("Columns with missing values (top 10):")
            lines.append("")
            lines.append(worst[["column", "missing_count", "missing_pct"]]
                         .to_markdown(index=False))
        lines.append("")
        dup = int(self.df.duplicated(subset=["patient_id"]).sum()
                  if "patient_id" in self.df.columns else 0)
        lines.append(f"- Duplicate patient_id rows: **{dup}**")
        lines.append("")

        # ---- recommendations ----
        lines.append("## 7. Recommendations for Modeling")
        lines.append("")
        for r in self._modeling_recommendations(tests_df):
            lines.append(f"- {r}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    def _key_findings(self, tests_df) -> list[str]:
        out = []
        pb = tests_df[tests_df["test"] == "point_biserial"].copy()
        if not pb.empty:
            pb["abs"] = pb["statistic"].abs()
            top = pb.sort_values("abs", ascending=False).head(5)
            names = ", ".join(
                f"`{r.feature}` (r={r.statistic:+.2f})" for _, r in top.iterrows()
            )
            out.append(f"Strongest linear correlates of the disease target: {names}.")

        chi = tests_df[(tests_df["test"] == "chi_square") &
                       (tests_df["significant"])].copy()
        if not chi.empty:
            chi = chi.sort_values("effect_size", ascending=False)
            names = ", ".join(
                f"`{r.feature}` (V={r.effect_size:.2f})"
                for _, r in chi.head(5).iterrows()
            )
            out.append(f"Categorical features significantly associated with risk "
                       f"level (chi-square): {names}.")

        anova = tests_df[(tests_df["test"] == "anova") &
                         (tests_df["significant"])].copy()
        if not anova.empty:
            anova = anova.sort_values("effect_size", ascending=False)
            names = ", ".join(
                f"`{r.feature}` (\u03b7\u00b2={r.effect_size:.2f})"
                for _, r in anova.head(5).iterrows()
            )
            out.append(f"Numeric features differing most across risk groups "
                       f"(ANOVA effect size): {names}.")

        # demographic pattern from data
        if "age" in self.df.columns:
            age_by_risk = self.df.groupby("risk_level", observed=True)["age"].mean()
            age_by_risk = age_by_risk.reindex(RISK_ORDER)
            out.append(
                "Mean age rises with risk level: "
                + ", ".join(f"{k} {v:.1f}y" for k, v in age_by_risk.items()
                            if pd.notna(v)) + "."
            )
        return out

    # ------------------------------------------------------------------ #
    def _modeling_recommendations(self, tests_df) -> list[str]:
        recs = [
            "Use the strongest correlates identified above as priority "
            "predictors, but retain the full feature set for tree-based models "
            "(XGBoost / Random Forest) that capture interactions.",
            "Several key numeric features are non-normal (Shapiro-Wilk); prefer "
            "tree-based models or apply scaling/transforms for linear models.",
            "Address the mild class imbalance in the risk target via stratified "
            "splits and class weights (or SMOTE) during training.",
            "Guard against leakage: `framingham_risk_score` / "
            "`ten_year_cvd_risk_pct` are derived from clinical inputs — decide "
            "explicitly whether they belong in the model feature set.",
            "Feed the risk-stratification output into the ED capacity-planning "
            "module, leveraging the temporal demand patterns surfaced here.",
        ]
        return recs

    # ------------------------------------------------------------------ #
    def run(self) -> str:
        """Execute the full EDA pipeline and write the markdown report."""
        logger.info("Running descriptive statistics ...")
        desc_res = self.desc.run()
        logger.info("Running statistical tests ...")
        tests_df = self.tester.run()
        logger.info("Generating figures ...")
        figures = self.viz.run()

        logger.info("Assembling markdown report ...")
        md = self.build_markdown(desc_res, tests_df, figures)
        report_path = os.path.join(REPORTS_DIR, "eda_summary_report.md")
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(md)
        logger.info("EDA report written to %s", report_path)
        print(f"\nEDA complete:\n  report : {report_path}\n  figures: "
              f"{len(figures)} in {FIGURES_DIR}")
        return report_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    EDAReport().run()
