"""
Forecast dashboard.

Builds a set of publication-quality figures summarising the load forecast,
staffing plan and cost optimisation, and assembles them into a single
self-contained HTML dashboard.
"""
from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.utils.common import get_logger, get_paths

logger = get_logger("forecast_dashboard")

BLUE, RED, ORANGE, GREEN, GREY = "#3A6EA5", "#D1495B", "#E08E45", "#2A9D8F", "#6C757D"


class ForecastDashboard:
    def __init__(self):
        self.paths = get_paths()
        self.fig_dir = self.paths["figures"]
        self.rep_dir = self.paths["reports"]

    def _read(self, name: str) -> pd.DataFrame:
        fp = self.rep_dir / name
        if not fp.exists():
            raise FileNotFoundError(f"{fp} not found. Run the forecasting pipeline first.")
        return pd.read_csv(fp)

    # ---------- figures ----------
    def _fig_staffing(self, sched: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(13, 6))
        w = pd.to_datetime(sched["week"])
        ax.bar(w, sched["physicians"], color=BLUE, label="Physicians")
        ax.bar(w, sched["nurses"], bottom=sched["physicians"], color=GREEN, label="Nurses")
        ax.bar(w, sched["admin_staff"],
               bottom=sched["physicians"] + sched["nurses"], color=ORANGE, label="Admin")
        ax.set_title("Weekly staffing plan (with surge buffer)", fontsize=14, weight="bold")
        ax.set_ylabel("Staff (FTE)")
        ax.legend()
        out = self.fig_dir / "forecast_staffing_plan.png"
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        return out

    def _fig_utilization(self, sched: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(13, 6))
        w = pd.to_datetime(sched["week"])
        colors = [RED if f else BLUE for f in sched["high_load_flag"]]
        ax.bar(w, sched["projected_utilization"], color=colors)
        ax.axhline(0.85, color=RED, ls="--", lw=1.5, label="High-load threshold (85%)")
        ax.set_title("Projected physician utilisation by week", fontsize=14, weight="bold")
        ax.set_ylabel("Utilisation")
        ax.legend()
        out = self.fig_dir / "forecast_utilization.png"
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        return out

    def _fig_cost(self, opt: pd.DataFrame) -> Path:
        fig, ax = plt.subplots(figsize=(13, 6))
        w = pd.to_datetime(opt["week"])
        ax.plot(w, opt["weekly_cost"], color=RED, marker="o", lw=2)
        ax.fill_between(w, 0, opt["weekly_cost"], color=RED, alpha=0.12)
        ax.set_title("Optimised weekly staffing cost", fontsize=14, weight="bold")
        ax.set_ylabel("Cost (USD / week)")
        out = self.fig_dir / "forecast_cost.png"
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
        return out

    @staticmethod
    def _b64(fp: Path) -> str:
        return base64.b64encode(Path(fp).read_bytes()).decode("ascii")

    # ---------- html ----------
    def run(self) -> Path:
        logger.info("=== Building forecast dashboard ===")
        fc = self._read("load_forecast.csv")
        sched = self._read("resource_schedule.csv")
        opt = self._read("schedule_optimization.csv")

        tot = fc[fc["series"] == "total"]
        figs = [
            ("Weekly visit-volume forecast", self.fig_dir / "forecast_total_visits.png"),
            ("Per-clinic forecast", self.fig_dir / "forecast_by_clinic.png"),
            ("Staffing plan", self._fig_staffing(sched)),
            ("Projected utilisation", self._fig_utilization(sched)),
            ("Optimised weekly cost", self._fig_cost(opt)),
        ]

        kpis = {
            "Forecast horizon (weeks)": len(tot),
            "Mean weekly visits (forecast)": f"{tot['forecast'].mean():.0f}",
            "Peak weekly visits (forecast)": f"{tot['forecast'].max():.0f}",
            "High-load weeks": int(sched["high_load_flag"].sum()),
            "Mean physicians / week": f"{sched['physicians'].mean():.1f}",
            "Total 12-week staffing cost": f"${opt['weekly_cost'].sum():,.0f}",
        }
        kpi_html = "".join(
            f'<div class="kpi"><div class="kpi-val">{v}</div>'
            f'<div class="kpi-lbl">{k}</div></div>' for k, v in kpis.items()
        )
        fig_html = "".join(
            f'<section><h2>{title}</h2>'
            f'<img src="data:image/png;base64,{self._b64(fp)}"/></section>'
            for title, fp in figs if Path(fp).exists()
        )

        html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Outpatient Load Forecast Dashboard</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
       margin:0;background:#f4f6f8;color:#212529}}
  header{{background:{BLUE};color:#fff;padding:28px 40px}}
  header h1{{margin:0;font-size:24px}} header p{{margin:6px 0 0;opacity:.9}}
  .kpis{{display:flex;flex-wrap:wrap;gap:16px;padding:28px 40px}}
  .kpi{{background:#fff;border-radius:10px;padding:18px 22px;min-width:170px;
        box-shadow:0 1px 4px rgba(0,0,0,.08);flex:1}}
  .kpi-val{{font-size:26px;font-weight:700;color:{BLUE}}}
  .kpi-lbl{{font-size:13px;color:{GREY};margin-top:4px}}
  section{{background:#fff;margin:20px 40px;padding:22px;border-radius:10px;
           box-shadow:0 1px 4px rgba(0,0,0,.08)}}
  section h2{{margin:0 0 14px;font-size:18px;color:#333}}
  section img{{width:100%;height:auto;border-radius:6px}}
  footer{{padding:24px 40px;color:{GREY};font-size:12px}}
</style></head><body>
<header><h1>Outpatient Clinic Load Forecast &amp; Staffing Dashboard</h1>
<p>Type 2 Diabetes complications programme &middot; 12-week resource plan</p></header>
<div class="kpis">{kpi_html}</div>
{fig_html}
<footer>Generated {datetime.now():%Y-%m-%d %H:%M} &middot; synthetic data &middot;
End-to-End Healthcare Data Engineering Framework</footer>
</body></html>"""

        out = self.rep_dir / "forecast_dashboard.html"
        out.write_text(html, encoding="utf-8")
        logger.info("Saved %s", out)
        logger.info("=== Dashboard done ===")
        return out


if __name__ == "__main__":
    ForecastDashboard().run()
