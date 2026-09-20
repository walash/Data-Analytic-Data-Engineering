"""Publication-quality EDA visualisations for the CVD project.

:class:`EDAVisualizer` renders ten figures (300 DPI, seaborn ``whitegrid``)
covering the risk-level target, demographics, correlations, ECG features,
cardiovascular risk scores, and ED temporal / seasonal demand. All figures are
saved to ``outputs/figures/``.
"""
from __future__ import annotations

import logging
import os

import matplotlib

matplotlib.use("Agg")  # headless backend for pipeline execution
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

from .common import (  # noqa: E402
    FIGURES_DIR,
    NUMERIC_FEATURES,
    RISK_ORDER,
    RISK_PALETTE,
    CHEST_PAIN_LABELS,
    FBS_LABELS,
    SEX_LABELS,
    ensure_dirs,
    label_series,
    load_ed_timeseries,
    load_ed_visits_raw,
    load_features,
)

logger = logging.getLogger(__name__)

sns.set_theme(style="whitegrid", context="notebook")
plt.rcParams.update(
    {
        "figure.dpi": 110,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "axes.titleweight": "bold",
        "axes.titlesize": 13,
        "axes.labelsize": 11,
        "font.family": "DejaVu Sans",
    }
)


class EDAVisualizer:
    """Generate and save all EDA figures."""

    def __init__(self, df: pd.DataFrame | None = None):
        ensure_dirs()
        self.df = df if df is not None else load_features()
        self.saved: list[str] = []

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _save(self, fig, name: str) -> str:
        path = os.path.join(FIGURES_DIR, name)
        fig.savefig(path)
        plt.close(fig)
        self.saved.append(path)
        logger.info("Saved figure %s", name)
        return path

    def _risk_col(self) -> pd.Series:
        return pd.Categorical(
            self.df["risk_level"], categories=RISK_ORDER, ordered=True
        )

    # ------------------------------------------------------------------ #
    # 1. Risk distribution (pie + bar)
    # ------------------------------------------------------------------ #
    def plot_risk_distribution(self) -> str:
        counts = self.df["risk_level"].value_counts().reindex(RISK_ORDER).fillna(0)
        colors = [RISK_PALETTE[r] for r in RISK_ORDER]
        fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

        axes[0].pie(
            counts.values,
            labels=RISK_ORDER,
            autopct="%1.1f%%",
            colors=colors,
            startangle=90,
            wedgeprops={"edgecolor": "white", "linewidth": 1.5},
            textprops={"fontsize": 11},
        )
        axes[0].set_title("CVD Risk Level Share")

        bars = axes[1].bar(RISK_ORDER, counts.values, color=colors,
                           edgecolor="white")
        axes[1].set_title("CVD Risk Level Counts")
        axes[1].set_xlabel("Risk Level")
        axes[1].set_ylabel("Number of Patients")
        for b, v in zip(bars, counts.values):
            axes[1].text(b.get_x() + b.get_width() / 2, v, f"{int(v):,}",
                         ha="center", va="bottom", fontsize=10)
        fig.suptitle("Cardiovascular Risk Level Distribution",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "01_risk_distribution.png")

    # ------------------------------------------------------------------ #
    # 2. Age group vs risk level heatmap
    # ------------------------------------------------------------------ #
    def plot_age_risk_heatmap(self) -> str:
        if "age_group" in self.df.columns:
            age = self.df["age_group"]
        else:
            age = pd.cut(self.df["age"], bins=[29, 39, 49, 59, 69, 120],
                         labels=["30-39", "40-49", "50-59", "60-69", "70+"])
        ct = pd.crosstab(age, self._risk_col(), normalize="index")
        ct = ct.reindex(columns=RISK_ORDER)
        fig, ax = plt.subplots(figsize=(9, 6))
        sns.heatmap(ct * 100, annot=True, fmt=".1f", cmap="RdYlGn_r",
                    cbar_kws={"label": "% within age group"}, ax=ax,
                    linewidths=0.5, linecolor="white")
        ax.set_title("Risk Level Composition by Age Group (%)")
        ax.set_xlabel("CVD Risk Level")
        ax.set_ylabel("Age Group")
        return self._save(fig, "02_age_risk_heatmap.png")

    # ------------------------------------------------------------------ #
    # 3. Correlation heatmap
    # ------------------------------------------------------------------ #
    def plot_correlation_heatmap(self) -> str:
        cols = [c for c in NUMERIC_FEATURES if c in self.df.columns]
        data = self.df[cols].apply(pd.to_numeric, errors="coerce").copy()
        data["cvd_risk_ord"] = self._risk_col().codes
        corr = data.corr(method="pearson")
        fig, ax = plt.subplots(figsize=(11, 9))
        mask = np.triu(np.ones_like(corr, dtype=bool))
        sns.heatmap(corr, mask=mask, annot=True, fmt=".2f", cmap="coolwarm",
                    center=0, vmin=-1, vmax=1, square=True, linewidths=0.5,
                    cbar_kws={"label": "Pearson r", "shrink": 0.8}, ax=ax,
                    annot_kws={"size": 8})
        ax.set_title("Pearson Correlation of Numeric Features (incl. CVD Risk)")
        return self._save(fig, "03_correlation_heatmap.png")

    # ------------------------------------------------------------------ #
    # 4. Key feature distributions by risk level
    # ------------------------------------------------------------------ #
    def plot_feature_distributions(self) -> str:
        feats = [("age", "Age (years)"),
                 ("cholesterol", "Serum Cholesterol (mg/dl)"),
                 ("resting_bp", "Resting Blood Pressure (mm Hg)"),
                 ("max_heart_rate", "Max Heart Rate (bpm)")]
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        for ax, (col, label) in zip(axes.ravel(), feats):
            for risk in RISK_ORDER:
                subset = self.df.loc[self.df["risk_level"] == risk, col].dropna()
                if subset.empty:
                    continue
                sns.histplot(subset, ax=ax, color=RISK_PALETTE[risk], label=risk,
                             kde=True, stat="density", element="step",
                             alpha=0.25, linewidth=1.5)
            ax.set_title(f"{label} by Risk Level")
            ax.set_xlabel(label)
            ax.set_ylabel("Density")
            ax.legend(title="Risk Level", fontsize=9)
        fig.suptitle("Key Clinical Feature Distributions by CVD Risk Level",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "04_feature_distributions.png")

    # ------------------------------------------------------------------ #
    # 5. Risk by demographics (sex, chest pain, FBS)
    # ------------------------------------------------------------------ #
    def plot_risk_by_demographics(self) -> str:
        specs = [("sex", SEX_LABELS, "Sex"),
                 ("chest_pain_type", CHEST_PAIN_LABELS, "Chest Pain Type"),
                 ("fasting_blood_sugar", FBS_LABELS, "Fasting Blood Sugar")]
        fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
        for ax, (col, mapping, label) in zip(axes, specs):
            tmp = self.df[[col, "risk_level"]].copy()
            tmp[col] = tmp[col].map(lambda v: mapping.get(v, v))
            ct = pd.crosstab(tmp[col], tmp["risk_level"], normalize="index")
            ct = ct.reindex(columns=RISK_ORDER).fillna(0)
            ct.plot(kind="bar", stacked=False, ax=ax,
                    color=[RISK_PALETTE[r] for r in RISK_ORDER], edgecolor="white")
            ax.set_title(f"Risk Distribution by {label}")
            ax.set_xlabel(label)
            ax.set_ylabel("Proportion within group")
            ax.tick_params(axis="x", rotation=20)
            ax.legend(title="Risk", fontsize=8)
        fig.suptitle("CVD Risk Distribution across Demographic / Clinical Groups",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "05_risk_by_demographics.png")

    # ------------------------------------------------------------------ #
    # 6. ECG-related box plots by risk level
    # ------------------------------------------------------------------ #
    def plot_ecg_analysis(self) -> str:
        feats = [("max_heart_rate", "Max Heart Rate (bpm)"),
                 ("st_depression", "ST Depression (oldpeak)"),
                 ("hr_reserve_ratio", "HR Reserve Ratio"),
                 ("ca_vessels", "Major Vessels Coloured (ca)")]
        feats = [(c, l) for c, l in feats if c in self.df.columns]
        fig, axes = plt.subplots(1, len(feats), figsize=(5 * len(feats), 5.5))
        if len(feats) == 1:
            axes = [axes]
        for ax, (col, label) in zip(axes, feats):
            sns.boxplot(data=self.df, x="risk_level", y=col, order=RISK_ORDER,
                        hue="risk_level", palette=RISK_PALETTE, legend=False,
                        ax=ax, showfliers=False)
            sns.stripplot(data=self.df.sample(min(800, len(self.df)), random_state=1),
                          x="risk_level", y=col, order=RISK_ORDER, ax=ax,
                          color="0.25", size=1.5, alpha=0.25)
            ax.set_title(label)
            ax.set_xlabel("Risk Level")
            ax.set_ylabel(label)
        fig.suptitle("ECG / Exercise-Test Features by CVD Risk Level",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "06_ecg_analysis.png")

    # ------------------------------------------------------------------ #
    # 7. Temporal ED patterns (hour, day-of-week, month)
    # ------------------------------------------------------------------ #
    def plot_temporal_ed_patterns(self) -> str:
        ev = load_ed_visits_raw()
        ts = ev["visit_timestamp"]
        ev = ev.assign(
            hour=ts.dt.hour,
            dow=ts.dt.dayofweek,
            month=ts.dt.month,
        )
        dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

        fig, axes = plt.subplots(1, 3, figsize=(19, 5.5))

        # hour x dow heatmap
        hd = ev.pivot_table(index="dow", columns="hour", values="visit_id",
                            aggfunc="count").reindex(range(7))
        sns.heatmap(hd, cmap="viridis", ax=axes[0],
                    cbar_kws={"label": "Visits"})
        axes[0].set_yticklabels(dow_names, rotation=0)
        axes[0].set_title("ED Visits by Hour \u00d7 Day of Week")
        axes[0].set_xlabel("Hour of Day")
        axes[0].set_ylabel("Day of Week")

        # visits by hour
        by_hour = ev.groupby("hour")["visit_id"].count()
        axes[1].plot(by_hour.index, by_hour.values, marker="o", color="#1f77b4")
        axes[1].fill_between(by_hour.index, by_hour.values, alpha=0.2,
                             color="#1f77b4")
        axes[1].set_title("ED Visits by Hour of Day")
        axes[1].set_xlabel("Hour of Day")
        axes[1].set_ylabel("Total Visits")
        axes[1].set_xticks(range(0, 24, 2))

        # visits by month
        by_month = ev.groupby("month")["visit_id"].count().reindex(range(1, 13))
        axes[2].bar(range(1, 13), by_month.values, color="#9467bd",
                    edgecolor="white")
        axes[2].set_title("ED Visits by Month")
        axes[2].set_xlabel("Month")
        axes[2].set_ylabel("Total Visits")
        axes[2].set_xticks(range(1, 13))
        axes[2].set_xticklabels(month_names, rotation=45)

        fig.suptitle("Emergency Department Temporal Demand Patterns",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "07_temporal_ed_patterns.png")

    # ------------------------------------------------------------------ #
    # 8. ED seasonal trend with rolling averages
    # ------------------------------------------------------------------ #
    def plot_ed_seasonal_trends(self) -> str:
        ts = load_ed_timeseries()
        daily = ts.groupby("visit_date", as_index=False)["visits"].sum()
        daily = daily.sort_values("visit_date")
        daily["roll7"] = daily["visits"].rolling(7, min_periods=1).mean()
        daily["roll30"] = daily["visits"].rolling(30, min_periods=1).mean()

        fig, ax = plt.subplots(figsize=(15, 6))
        ax.plot(daily["visit_date"], daily["visits"], color="0.7", linewidth=0.8,
                label="Daily visits")
        ax.plot(daily["visit_date"], daily["roll7"], color="#1f77b4",
                linewidth=1.8, label="7-day rolling avg")
        ax.plot(daily["visit_date"], daily["roll30"], color="#d62728",
                linewidth=2.2, label="30-day rolling avg")
        ax.set_title("Emergency Department Daily Visit Volume & Rolling Trends")
        ax.set_xlabel("Date")
        ax.set_ylabel("Visits per Day")
        ax.legend()
        fig.autofmt_xdate()
        return self._save(fig, "08_ed_seasonal_trends.png")

    # ------------------------------------------------------------------ #
    # 9. Cholesterol vs BP scatter with regression by risk
    # ------------------------------------------------------------------ #
    def plot_cholesterol_bp_scatter(self) -> str:
        fig, ax = plt.subplots(figsize=(10, 7))
        for risk in RISK_ORDER:
            sub = self.df[self.df["risk_level"] == risk]
            if sub.empty:
                continue
            sns.regplot(data=sub, x="cholesterol", y="resting_bp", ax=ax,
                        scatter_kws={"alpha": 0.25, "s": 14,
                                     "color": RISK_PALETTE[risk]},
                        line_kws={"color": RISK_PALETTE[risk], "linewidth": 2},
                        label=risk, ci=None)
        ax.set_title("Serum Cholesterol vs Resting BP by CVD Risk Level")
        ax.set_xlabel("Serum Cholesterol (mg/dl)")
        ax.set_ylabel("Resting Blood Pressure (mm Hg)")
        ax.legend(title="Risk Level")
        return self._save(fig, "09_cholesterol_bp_scatter.png")

    # ------------------------------------------------------------------ #
    # 10. Framingham / CVD risk-score distribution by risk group
    # ------------------------------------------------------------------ #
    def plot_framingham_score_dist(self) -> str:
        score_col = ("framingham_risk_score"
                     if "framingham_risk_score" in self.df.columns
                     else "ten_year_cvd_risk_pct")
        fig, axes = plt.subplots(1, 2, figsize=(15, 6))

        sns.violinplot(data=self.df, x="risk_level", y=score_col,
                       order=RISK_ORDER, hue="risk_level", palette=RISK_PALETTE,
                       legend=False, ax=axes[0], inner="quartile", cut=0)
        axes[0].set_title(f"{score_col} by Risk Level (violin)")
        axes[0].set_xlabel("Risk Level")
        axes[0].set_ylabel("Cardiovascular Risk Score")

        for risk in RISK_ORDER:
            sub = self.df.loc[self.df["risk_level"] == risk, score_col].dropna()
            if sub.empty:
                continue
            sns.kdeplot(sub, ax=axes[1], color=RISK_PALETTE[risk], fill=True,
                        alpha=0.25, linewidth=2, label=risk)
        axes[1].set_title(f"{score_col} Density by Risk Level")
        axes[1].set_xlabel("Cardiovascular Risk Score")
        axes[1].set_ylabel("Density")
        axes[1].legend(title="Risk Level")

        if "ten_year_cvd_risk_pct" in self.df.columns and score_col != \
                "ten_year_cvd_risk_pct":
            pass
        fig.suptitle("Computed Cardiovascular Risk Score Distribution",
                     fontsize=15, fontweight="bold")
        return self._save(fig, "10_framingham_score_dist.png")

    # ------------------------------------------------------------------ #
    # orchestration
    # ------------------------------------------------------------------ #
    def run(self) -> list[str]:
        """Generate every figure and return the list of saved paths."""
        methods = [
            self.plot_risk_distribution,
            self.plot_age_risk_heatmap,
            self.plot_correlation_heatmap,
            self.plot_feature_distributions,
            self.plot_risk_by_demographics,
            self.plot_ecg_analysis,
            self.plot_temporal_ed_patterns,
            self.plot_ed_seasonal_trends,
            self.plot_cholesterol_bp_scatter,
            self.plot_framingham_score_dist,
        ]
        for m in methods:
            try:
                m()
            except Exception:  # pragma: no cover - keep pipeline resilient
                logger.exception("Failed to render %s", m.__name__)
        logger.info("Generated %d figures", len(self.saved))
        return self.saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    paths = EDAVisualizer().run()
    print(f"Saved {len(paths)} figures to outputs/figures/")
