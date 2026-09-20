"""Visualisation dashboard for the ED capacity-planning module.

:class:`CapacityDashboard` renders eight publication-quality figures from the
demand forecast and resource-allocation plan, then assembles an HTML summary
dashboard that embeds them alongside headline KPIs.

Figures (saved to ``outputs/figures/ed_capacity_*.png``)
-------------------------------------------------------
1. 60-day demand forecast by risk group (stacked area).
2. Daily resource-requirement heatmap (resources x days).
3. Capacity-utilisation gauge.
4. High-demand alert calendar.
5. Risk-group proportion trend.
6. Bed-occupancy forecast (regular / monitored / ICU).
7. Staff-allocation timeline.
8. Weekly ED visit pattern (historical vs forecast).

HTML dashboard -> ``outputs/reports/capacity_dashboard.html``.
"""
from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import seaborn as sns  # noqa: E402

from . import common  # noqa: E402

logger = logging.getLogger(__name__)
sns.set_theme(style="whitegrid")


class CapacityDashboard:
    """Build the ED capacity-planning figures and HTML dashboard."""

    def __init__(
        self,
        forecast: pd.DataFrame,
        plan: pd.DataFrame,
        history: pd.DataFrame | None = None,
        summary: Dict | None = None,
    ):
        self.forecast = forecast
        self.plan = plan
        self.history = history if history is not None else common.wide_demand()
        self.summary = summary or {}
        self.figures: Dict[str, str] = {}
        common.ensure_dirs()

    # ------------------------------------------------------------------ #
    def _fc_wide(self) -> pd.DataFrame:
        wide = self.forecast.pivot_table(
            index="date", columns="risk_level", values="forecast", aggfunc="sum"
        ).reindex(columns=common.RISK_ORDER).fillna(0.0)
        return wide

    def _save(self, fig, name: str) -> str:
        path = os.path.join(common.FIGURES_DIR, name)
        fig.tight_layout()
        fig.savefig(path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        self.figures[name] = path
        logger.info("Saved figure -> %s", path)
        return path

    # ------------------------------------------------------------------ #
    # 1. Stacked-area demand forecast
    # ------------------------------------------------------------------ #
    def plot_demand_forecast(self) -> str:
        wide = self._fc_wide()
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.stackplot(
            wide.index,
            [wide[g] for g in common.RISK_ORDER],
            labels=common.RISK_ORDER,
            colors=[common.RISK_COLORS[g] for g in common.RISK_ORDER],
            alpha=0.85,
        )
        ax.set_title("60-Day ED Demand Forecast by CVD Risk Group")
        ax.set_xlabel("Date")
        ax.set_ylabel("Forecasted daily ED visits")
        ax.legend(loc="upper left", title="Risk group")
        fig.autofmt_xdate()
        return self._save(fig, "ed_capacity_01_demand_forecast_stacked.png")

    # ------------------------------------------------------------------ #
    # 2. Resource-requirement heatmap
    # ------------------------------------------------------------------ #
    def plot_resource_heatmap(self) -> str:
        cols = [
            "icu_beds_needed", "monitored_beds_needed", "standard_beds_needed",
            "physicians_needed", "nurses_needed", "techs_needed",
            "ecg_needed", "echo_needed", "lab_draws_needed",
        ]
        labels = [
            "ICU beds", "Monitored beds", "Standard beds",
            "Physicians", "Nurses", "Techs",
            "ECG units", "Echo units", "Lab draws",
        ]
        sub = self.plan[cols].head(30).T
        sub.index = labels
        # Normalise each row 0-1 for comparable colour scale.
        norm = sub.div(sub.max(axis=1).replace(0, 1), axis=0)
        fig, ax = plt.subplots(figsize=(14, 6))
        sns.heatmap(norm, cmap="YlOrRd", cbar_kws={"label": "Relative need"},
                    ax=ax, linewidths=0.3, linecolor="white",
                    xticklabels=[d.strftime("%m-%d") for d in sub.columns])
        ax.set_title("Daily Resource Requirements (first 30 days, row-normalised)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Resource")
        plt.xticks(rotation=90)
        return self._save(fig, "ed_capacity_02_resource_heatmap.png")

    # ------------------------------------------------------------------ #
    # 3. Utilisation gauge
    # ------------------------------------------------------------------ #
    def plot_utilization_gauge(self) -> str:
        mean_util = float(self.plan["capacity_utilization"].mean())
        peak_util = float(self.plan["capacity_utilization"].max())
        fig, ax = plt.subplots(figsize=(8, 5), subplot_kw={"projection": "polar"})
        # Half-circle gauge from 0 to 1 (mapped to pi..0).
        theta = np.linspace(np.pi, 0, 100)
        zones = [(0.0, 0.7, "#2ca02c"), (0.7, 0.85, "#ff7f0e"),
                 (0.85, 1.2, "#d62728")]
        for lo, hi, color in zones:
            mask = (np.linspace(0, 1.2, 100) >= lo) & (np.linspace(0, 1.2, 100) <= hi)
            ax.plot(theta[mask], np.ones(mask.sum()) * 1.0, lw=18, color=color,
                    solid_capstyle="butt")
        # Needle for mean utilisation.
        val = min(mean_util, 1.2)
        needle = np.pi - (val / 1.2) * np.pi
        ax.plot([needle, needle], [0, 0.9], color="black", lw=3)
        ax.set_ylim(0, 1.2)
        ax.set_yticks([])
        ax.set_xticks([])
        ax.set_title(
            f"Mean Capacity Utilisation: {mean_util:.0%}\n"
            f"(Peak: {peak_util:.0%}  |  Alert threshold: 85%)",
            pad=20,
        )
        return self._save(fig, "ed_capacity_03_utilization_gauge.png")

    # ------------------------------------------------------------------ #
    # 4. High-demand alert calendar
    # ------------------------------------------------------------------ #
    def plot_alert_calendar(self) -> str:
        util = self.plan["capacity_utilization"].copy()
        df = pd.DataFrame({"util": util})
        df["week"] = ((df.index - df.index.min()).days // 7)
        df["dow"] = df.index.dayofweek
        grid = df.pivot_table(index="week", columns="dow", values="util")
        grid = grid.reindex(columns=range(7))
        fig, ax = plt.subplots(figsize=(10, max(4, len(grid) * 0.5)))
        sns.heatmap(
            grid, cmap="RdYlGn_r", center=0.85, vmin=0.4, vmax=1.1,
            linewidths=1, linecolor="white", cbar_kws={"label": "Utilisation"},
            xticklabels=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            ax=ax,
        )
        ax.set_title("High-Demand Alert Calendar (red = utilisation > 85%)")
        ax.set_xlabel("Day of week")
        ax.set_ylabel("Forecast week")
        return self._save(fig, "ed_capacity_04_alert_calendar.png")

    # ------------------------------------------------------------------ #
    # 5. Risk-group proportion trend
    # ------------------------------------------------------------------ #
    def plot_risk_proportion(self) -> str:
        wide = self._fc_wide()
        prop = wide.div(wide.sum(axis=1).replace(0, 1), axis=0)
        prop = prop.rolling(7, min_periods=1).mean()
        fig, ax = plt.subplots(figsize=(12, 5))
        bottom = np.zeros(len(prop))
        for g in common.RISK_ORDER:
            ax.fill_between(prop.index, bottom, bottom + prop[g].values,
                            color=common.RISK_COLORS[g], alpha=0.8, label=g)
            bottom = bottom + prop[g].values
        ax.set_ylim(0, 1)
        ax.set_title("Risk-Group Proportion Trend (7-day smoothed, forecast)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Share of daily demand")
        ax.legend(loc="upper left", title="Risk group")
        fig.autofmt_xdate()
        return self._save(fig, "ed_capacity_05_risk_proportion_trend.png")

    # ------------------------------------------------------------------ #
    # 6. Bed-occupancy forecast
    # ------------------------------------------------------------------ #
    def plot_bed_occupancy(self) -> str:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(self.plan.index, self.plan["standard_beds_needed"],
                label="Standard", color="#2ca02c")
        ax.plot(self.plan.index, self.plan["monitored_beds_needed"],
                label="Monitored", color="#ff7f0e")
        ax.plot(self.plan.index, self.plan["icu_beds_needed"],
                label="ICU", color="#d62728")
        ax.plot(self.plan.index, self.plan["total_beds_needed"],
                label="Total", color="#1f77b4", lw=2.2, ls="--")
        ax.set_title("Bed-Occupancy Forecast by Bed Type (surge-adjusted)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Beds needed")
        ax.legend(loc="upper left")
        fig.autofmt_xdate()
        return self._save(fig, "ed_capacity_06_bed_occupancy.png")

    # ------------------------------------------------------------------ #
    # 7. Staff-allocation timeline
    # ------------------------------------------------------------------ #
    def plot_staff_timeline(self) -> str:
        fig, ax = plt.subplots(figsize=(12, 5))
        ax.plot(self.plan.index, self.plan["physicians_needed"],
                label="Physicians", color="#8c564b", marker=".", ms=3)
        ax.plot(self.plan.index, self.plan["nurses_needed"],
                label="Nurses", color="#9467bd", marker=".", ms=3)
        ax.plot(self.plan.index, self.plan["techs_needed"],
                label="Techs", color="#17becf", marker=".", ms=3)
        ax.set_title("Staff-Allocation Timeline (surge-adjusted daily needs)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Staff needed")
        ax.legend(loc="upper left")
        fig.autofmt_xdate()
        return self._save(fig, "ed_capacity_07_staff_timeline.png")

    # ------------------------------------------------------------------ #
    # 8. Weekly visit pattern: historical vs forecast
    # ------------------------------------------------------------------ #
    def plot_weekly_pattern(self) -> str:
        hist_total = self.history["Total"]
        fc_wide = self._fc_wide()
        fc_total = fc_wide.sum(axis=1)
        hist_dow = hist_total.groupby(hist_total.index.dayofweek).mean()
        fc_dow = fc_total.groupby(fc_total.index.dayofweek).mean()
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        x = np.arange(7)
        w = 0.4
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.bar(x - w / 2, hist_dow.reindex(range(7)).values, width=w,
               label="Historical avg", color="#4c72b0")
        ax.bar(x + w / 2, fc_dow.reindex(range(7)).values, width=w,
               label="Forecast avg", color="#dd8452")
        ax.set_xticks(x)
        ax.set_xticklabels(days)
        ax.set_title("Weekly ED Visit Pattern: Historical vs Forecast")
        ax.set_xlabel("Day of week")
        ax.set_ylabel("Avg daily ED visits")
        ax.legend()
        return self._save(fig, "ed_capacity_08_weekly_pattern.png")

    # ------------------------------------------------------------------ #
    def build_all_figures(self) -> Dict[str, str]:
        self.plot_demand_forecast()
        self.plot_resource_heatmap()
        self.plot_utilization_gauge()
        self.plot_alert_calendar()
        self.plot_risk_proportion()
        self.plot_bed_occupancy()
        self.plot_staff_timeline()
        self.plot_weekly_pattern()
        return self.figures

    # ------------------------------------------------------------------ #
    def _img_b64(self, path: str) -> str:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")

    def build_html(self) -> str:
        if not self.figures:
            self.build_all_figures()
        s = self.summary
        kpi_items = [
            ("Forecast horizon", f"{s.get('days', len(self.plan))} days"),
            ("Total forecast visits", f"{s.get('total_forecast_visits', 0):,.0f}"),
            ("High-demand days", str(s.get("high_demand_days", 0))),
            ("Peak total beds", str(s.get("peak_total_beds", 0))),
            ("Peak ICU beds", str(s.get("peak_icu_beds", 0))),
            ("Peak physicians", str(s.get("peak_physicians", 0))),
            ("Peak nurses", str(s.get("peak_nurses", 0))),
            ("Mean utilisation", f"{s.get('mean_utilization', 0):.0%}"),
            ("Peak utilisation", f"{s.get('peak_utilization', 0):.0%}"),
        ]
        kpi_html = "".join(
            f'<div class="kpi"><div class="kpi-val">{v}</div>'
            f'<div class="kpi-label">{k}</div></div>'
            for k, v in kpi_items
        )
        titles = {
            "ed_capacity_01_demand_forecast_stacked.png":
                "60-Day Demand Forecast by Risk Group",
            "ed_capacity_02_resource_heatmap.png":
                "Daily Resource Requirements Heatmap",
            "ed_capacity_03_utilization_gauge.png": "Capacity Utilisation Gauge",
            "ed_capacity_04_alert_calendar.png": "High-Demand Alert Calendar",
            "ed_capacity_05_risk_proportion_trend.png":
                "Risk-Group Proportion Trend",
            "ed_capacity_06_bed_occupancy.png": "Bed-Occupancy Forecast",
            "ed_capacity_07_staff_timeline.png": "Staff-Allocation Timeline",
            "ed_capacity_08_weekly_pattern.png":
                "Weekly Visit Pattern (Historical vs Forecast)",
        }
        fig_html = ""
        for name, title in titles.items():
            path = self.figures.get(name)
            if path and os.path.exists(path):
                b64 = self._img_b64(path)
                fig_html += (
                    f'<div class="card"><h3>{title}</h3>'
                    f'<img src="data:image/png;base64,{b64}" /></div>'
                )
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ED Capacity Planning Dashboard</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
         margin: 0; background: #f5f6f8; color: #1c2b36; }}
  header {{ background: linear-gradient(135deg,#1f4e79,#2e75b6); color:#fff;
            padding: 28px 40px; }}
  header h1 {{ margin: 0 0 6px; font-size: 26px; }}
  header p {{ margin: 0; opacity: 0.9; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 16px; padding: 24px 40px; }}
  .kpi {{ background:#fff; border-radius:10px; padding:18px 22px; min-width:150px;
          box-shadow:0 1px 4px rgba(0,0,0,0.08); flex:1; }}
  .kpi-val {{ font-size: 26px; font-weight: 700; color:#1f4e79; }}
  .kpi-label {{ font-size: 13px; color:#5b6b78; margin-top:4px; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(480px,1fr));
           gap:22px; padding: 8px 40px 48px; }}
  .card {{ background:#fff; border-radius:10px; padding:16px;
           box-shadow:0 1px 4px rgba(0,0,0,0.08); }}
  .card h3 {{ margin:0 0 12px; font-size:16px; color:#1f4e79; }}
  .card img {{ width:100%; height:auto; border-radius:6px; }}
  footer {{ text-align:center; color:#8794a1; padding:24px; font-size:13px; }}
</style></head>
<body>
  <header>
    <h1>Emergency Department Capacity Planning Dashboard</h1>
    <p>CVD Risk-Stratified Demand Forecasting &amp; Resource Allocation &middot;
       generated {generated}</p>
  </header>
  <section class="kpis">{kpi_html}</section>
  <section class="grid">{fig_html}</section>
  <footer>Auto-generated by <code>src/ed_capacity/capacity_dashboard.py</code>
    &middot; Data Analytics &amp; Data Engineering project</footer>
</body></html>"""
        common.ensure_dirs()
        out = os.path.join(common.REPORTS_DIR, "capacity_dashboard.html")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(html)
        logger.info("Saved HTML dashboard -> %s", out)
        return out

    def run(self) -> str:
        self.build_all_figures()
        return self.build_html()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    from .demand_forecaster import DemandForecaster
    from .resource_allocator import ResourceAllocator

    forecaster = DemandForecaster()
    fc = forecaster.run()
    allocator = ResourceAllocator()
    plan = allocator.run(fc)
    dash = CapacityDashboard(fc, plan, history=forecaster.history,
                             summary=allocator.summary(plan))
    dash.run()


if __name__ == "__main__":
    main()
