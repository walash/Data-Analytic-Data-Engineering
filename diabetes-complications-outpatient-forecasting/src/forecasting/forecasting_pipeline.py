"""
Forecasting pipeline orchestrator.

Runs the full outpatient-load forecasting workflow end to end:
    1. LoadForecaster       -> load_forecast.csv, forecast figures
    2. ResourceScheduler    -> resource_schedule.csv
    3. ScheduleOptimizer    -> schedule_optimization.csv
    4. ForecastDashboard    -> forecast_dashboard.html
and writes a Markdown summary report (outpatient_forecast_report.md).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.forecasting.forecast_dashboard import ForecastDashboard
from src.forecasting.load_forecaster import LoadForecaster
from src.forecasting.resource_scheduler import ResourceScheduler
from src.forecasting.schedule_optimizer import ScheduleOptimizer
from src.utils.common import get_logger, get_paths

logger = get_logger("forecasting_pipeline")


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    head = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows = ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join([head, sep, *rows])


def build_report(forecast, sched, opt, paths) -> str:
    tot = forecast[forecast["series"] == "total"]
    method = tot["method"].iloc[0]
    n_high = int(sched["high_load_flag"].sum())
    total_cost = opt["weekly_cost"].sum()

    plan = sched[["week", "forecast_visits", "physicians", "nurses",
                  "admin_staff", "projected_utilization", "high_load_flag"]].copy()
    plan["projected_utilization"] = (plan["projected_utilization"] * 100).round(1).astype(str) + "%"

    lines = [
        "# Outpatient Clinic Load Forecast & Resource Plan",
        "",
        f"_Generated: {datetime.now():%Y-%m-%d %H:%M} | synthetic data_",
        "",
        "## 1. Executive summary",
        "",
        f"- **Forecast model:** {method}",
        f"- **Horizon:** {len(tot)} weeks",
        f"- **Mean forecast demand:** {tot['forecast'].mean():.0f} visits/week "
        f"(peak {tot['forecast'].max():.0f})",
        f"- **High-load weeks (utilisation ≥ 85%):** {n_high}",
        f"- **Mean staffing:** {sched['physicians'].mean():.1f} physicians, "
        f"{sched['nurses'].mean():.1f} nurses, {sched['admin_staff'].mean():.1f} admin per week",
        f"- **Total 12-week optimised staffing cost:** ${total_cost:,.0f}",
        "",
        "## 2. Methodology",
        "",
        "Weekly outpatient demand is forecast with an ensemble of Facebook Prophet "
        "and a seasonal SARIMA model (weighted average), with automatic fallback to "
        "SARIMA-only or a seasonal-naive model. Forecast demand is converted into "
        "role-level staffing requirements using productivity ratios, a surge buffer "
        "is applied, and a linear program (PuLP/CBC) minimises weekly staffing cost "
        "subject to meeting the buffered demand for each role.",
        "",
        "## 3. Weekly resource plan",
        "",
        _md_table(plan),
        "",
        "## 4. Cost optimisation",
        "",
        _md_table(opt[["week", "forecast_visits", "physicians", "nurses",
                       "admin_staff", "weekly_cost"]]),
        "",
        "## 5. Artefacts",
        "",
        "- `outputs/reports/load_forecast.csv` — weekly forecast (total + per clinic)",
        "- `outputs/reports/resource_schedule.csv` — staffing plan & utilisation",
        "- `outputs/reports/schedule_optimization.csv` — cost-optimal staffing",
        "- `outputs/reports/forecast_dashboard.html` — interactive dashboard",
        "- `outputs/figures/forecast_*.png` — forecast & staffing figures",
        "",
    ]
    return "\n".join(lines)


def run() -> None:
    logger.info("========== FORECASTING PIPELINE START ==========")
    paths = get_paths()

    forecast = LoadForecaster().run()
    sched = ResourceScheduler().run()
    opt = ScheduleOptimizer().run()
    ForecastDashboard().run()

    report = build_report(forecast, sched, opt, paths)
    out = paths["reports"] / "outpatient_forecast_report.md"
    out.write_text(report, encoding="utf-8")
    logger.info("Saved %s", out)
    logger.info("========== FORECASTING PIPELINE DONE ==========")


if __name__ == "__main__":
    run()
