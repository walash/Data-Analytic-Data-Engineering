"""Publication-quality EDA visualizations for the T2D framework."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from src.eda.common import (NUMERIC_FEATURES, load_features, load_weekly_demand,
                            savefig, set_style)
from src.utils.common import COMPLICATION_CLASSES, get_logger

logger = get_logger("visualizations")


class EDAVisualizer:
    def __init__(self, df: pd.DataFrame | None = None):
        set_style()
        self.df = df if df is not None else load_features()
        self.saved: list[str] = []

    def _track(self, path: str):
        self.saved.append(path)

    def plot_complication_distribution(self):
        fig, ax = plt.subplots(1, 2, figsize=(14, 6))
        order = [c for c in COMPLICATION_CLASSES if c in self.df["complication"].unique()]
        vc = self.df["complication"].value_counts().reindex(order)
        ax[0].pie(vc, labels=vc.index, autopct="%1.1f%%", startangle=90,
                  colors=sns.color_palette("Set2", len(vc)))
        ax[0].set_title("Complication class distribution")
        sns.barplot(x=vc.index, y=vc.values, ax=ax[1], palette="Set2")
        ax[1].set_title("Complication counts")
        ax[1].set_ylabel("Patients")
        ax[1].tick_params(axis="x", rotation=30)
        fig.suptitle("Type 2 Diabetes Complication Distribution", fontsize=16, weight="bold")
        self._track(savefig(fig, "eda_01_complication_distribution.png"))

    def plot_deterioration_by_group(self):
        fig, ax = plt.subplots(1, 2, figsize=(15, 6))
        rate = self.df.groupby("age_group")["deteriorated_12m"].mean().reindex(
            ["<45", "45-54", "55-64", "65-74", "75+"])
        sns.barplot(x=rate.index, y=rate.values, ax=ax[0], palette="flare")
        ax[0].set_title("12-month deterioration rate by age group")
        ax[0].set_ylabel("Deterioration rate")
        rate2 = self.df.groupby("complication")["deteriorated_12m"].mean().sort_values()
        sns.barplot(x=rate2.values, y=rate2.index, ax=ax[1], palette="crest")
        ax[1].set_title("Deterioration rate by complication")
        ax[1].set_xlabel("Deterioration rate")
        self._track(savefig(fig, "eda_02_deterioration_rates.png"))

    def plot_correlation_heatmap(self):
        cols = [c for c in NUMERIC_FEATURES if c in self.df] + ["deteriorated_12m"]
        corr = self.df[cols].corr()
        fig, ax = plt.subplots(figsize=(14, 12))
        sns.heatmap(corr, cmap="RdBu_r", center=0, annot=False, square=True,
                    linewidths=0.5, cbar_kws={"shrink": 0.8}, ax=ax)
        ax.set_title("Pearson correlation matrix (numeric features + deterioration)",
                     fontsize=15, weight="bold")
        self._track(savefig(fig, "eda_03_correlation_heatmap.png"))

    def plot_key_distributions(self):
        keys = ["hba1c", "egfr", "systolic_bp", "urine_acr"]
        fig, axes = plt.subplots(2, 2, figsize=(15, 11))
        for ax, k in zip(axes.ravel(), keys):
            for det, sub in self.df.groupby("deteriorated_12m"):
                sns.kdeplot(sub[k], ax=ax, fill=True, alpha=0.4,
                            label="Deteriorated" if det else "Stable")
            ax.set_title(f"{k} by deterioration status")
            ax.legend()
        fig.suptitle("Key biomarker distributions by outcome", fontsize=16, weight="bold")
        self._track(savefig(fig, "eda_04_key_distributions.png"))

    def plot_risk_factor_boxplots(self):
        keys = ["hba1c", "egfr", "ldl", "comorbidity_index"]
        fig, axes = plt.subplots(2, 2, figsize=(15, 11))
        order = [c for c in COMPLICATION_CLASSES if c in self.df["complication"].unique()]
        for ax, k in zip(axes.ravel(), keys):
            sns.boxplot(data=self.df, x="complication", y=k, order=order, ax=ax, palette="Set3")
            ax.set_title(f"{k} across complication classes")
            ax.tick_params(axis="x", rotation=30)
        fig.suptitle("Risk-factor spread by complication", fontsize=16, weight="bold")
        self._track(savefig(fig, "eda_05_risk_factor_boxplots.png"))

    def plot_hba1c_egfr_scatter(self):
        fig, ax = plt.subplots(figsize=(12, 8))
        sns.scatterplot(data=self.df.sample(min(3000, len(self.df)), random_state=1),
                        x="hba1c", y="egfr", hue="deteriorated_12m",
                        palette={0: "#4C9F70", 1: "#D1495B"}, alpha=0.6, ax=ax)
        ax.set_title("HbA1c vs eGFR coloured by deterioration", fontsize=15, weight="bold")
        ax.legend(title="Deteriorated")
        self._track(savefig(fig, "eda_06_hba1c_egfr_scatter.png"))

    def plot_demographic_breakdown(self):
        fig, axes = plt.subplots(1, 3, figsize=(18, 6))
        for ax, col in zip(axes, ["sex", "smoking_status", "insurance_type"]):
            ct = pd.crosstab(self.df[col], self.df["deteriorated_12m"], normalize="index")
            ct.plot(kind="bar", stacked=True, ax=ax, colormap="coolwarm", legend=False)
            ax.set_title(f"Deterioration by {col}")
            ax.set_ylabel("Proportion")
            ax.tick_params(axis="x", rotation=20)
        axes[-1].legend(["Stable", "Deteriorated"], loc="lower right")
        fig.suptitle("Outcome by demographic strata", fontsize=16, weight="bold")
        self._track(savefig(fig, "eda_07_demographic_breakdown.png"))

    def plot_weekly_demand_trend(self):
        wk = load_weekly_demand()
        wk["week"] = pd.to_datetime(wk["week"])
        fig, ax = plt.subplots(figsize=(15, 6))
        ax.plot(wk["week"], wk["total_visits"], color="#3A6EA5", label="Weekly visits")
        ax.plot(wk["week"], wk["total_visits"].rolling(4, min_periods=1).mean(),
                color="#D1495B", lw=2.5, label="4-week rolling mean")
        ax.set_title("Historical weekly outpatient visit volume", fontsize=15, weight="bold")
        ax.set_ylabel("Visits / week")
        ax.legend()
        self._track(savefig(fig, "eda_08_weekly_demand_trend.png"))

    def plot_clinic_seasonality(self):
        wk = load_weekly_demand()
        wk["week"] = pd.to_datetime(wk["week"])
        wk["month"] = wk["week"].dt.month
        clinic_cols = [c for c in wk.columns if c not in ("week", "total_visits", "month")]
        monthly = wk.groupby("month")[clinic_cols].mean()
        fig, ax = plt.subplots(figsize=(14, 7))
        sns.heatmap(monthly.T, cmap="YlOrRd", annot=True, fmt=".0f", ax=ax,
                    cbar_kws={"label": "avg visits/week"})
        ax.set_title("Average weekly visits by clinic and month", fontsize=15, weight="bold")
        ax.set_xlabel("Month")
        self._track(savefig(fig, "eda_09_clinic_seasonality.png"))

    def plot_flag_prevalence(self):
        flags = ["ckd_flag", "obese_flag", "poor_glycemic_flag", "hypertension_flag",
                 "albuminuria_flag", "high_risk_ldl_flag"]
        prev = self.df[flags].mean().sort_values()
        fig, ax = plt.subplots(figsize=(12, 7))
        sns.barplot(x=prev.values, y=[f.replace("_flag", "") for f in prev.index],
                    palette="viridis", ax=ax)
        for i, v in enumerate(prev.values):
            ax.text(v + 0.005, i, f"{v:.1%}", va="center")
        ax.set_title("Clinical risk-flag prevalence in cohort", fontsize=15, weight="bold")
        ax.set_xlabel("Prevalence")
        self._track(savefig(fig, "eda_10_flag_prevalence.png"))

    def run_all(self) -> list[str]:
        logger.info("Generating EDA visualizations")
        self.plot_complication_distribution()
        self.plot_deterioration_by_group()
        self.plot_correlation_heatmap()
        self.plot_key_distributions()
        self.plot_risk_factor_boxplots()
        self.plot_hba1c_egfr_scatter()
        self.plot_demographic_breakdown()
        self.plot_weekly_demand_trend()
        self.plot_clinic_seasonality()
        self.plot_flag_prevalence()
        logger.info("Saved %d figures", len(self.saved))
        return self.saved


if __name__ == "__main__":
    EDAVisualizer().run_all()
