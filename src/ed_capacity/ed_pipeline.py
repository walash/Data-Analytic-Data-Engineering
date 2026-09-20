"""End-to-end orchestrator for the ED capacity-planning module.

Runs the full pipeline in sequence:

1. :class:`DemandForecaster`  -> 60-day demand forecast by risk group.
2. :class:`ResourceAllocator` -> daily bed/staff/equipment/lab plan + alerts.
3. :class:`CapacityOptimizer` -> cost-minimised 7-day staffing schedule.
4. :class:`CapacityDashboard` -> 8 figures + HTML dashboard.
5. Markdown summary report -> ``outputs/reports/ed_capacity_report.md``.

Run with::

    python -m src.ed_capacity.ed_pipeline
    # or
    python src/ed_capacity/ed_pipeline.py
"""
from __future__ import annotations

import logging
import os
import sys

import pandas as pd

# Allow running as a script (python src/ed_capacity/ed_pipeline.py).
if __package__ in (None, ""):
    sys.path.insert(
        0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    )
    from src.ed_capacity import common
    from src.ed_capacity.capacity_dashboard import CapacityDashboard
    from src.ed_capacity.capacity_optimizer import CapacityOptimizer
    from src.ed_capacity.demand_forecaster import DemandForecaster
    from src.ed_capacity.resource_allocator import ResourceAllocator
else:
    from . import common
    from .capacity_dashboard import CapacityDashboard
    from .capacity_optimizer import CapacityOptimizer
    from .demand_forecaster import DemandForecaster
    from .resource_allocator import ResourceAllocator

logger = logging.getLogger(__name__)


class EDPipeline:
    """Coordinate the full ED capacity-planning workflow."""

    def __init__(self):
        common.ensure_dirs()
        self.forecast: pd.DataFrame | None = None
        self.plan: pd.DataFrame | None = None
        self.schedule: pd.DataFrame | None = None
        self.summary: dict = {}

    def run(self) -> dict:
        logger.info("=" * 70)
        logger.info("ED CAPACITY PLANNING PIPELINE - START")
        logger.info("=" * 70)

        # 1. Demand forecasting -------------------------------------------- #
        forecaster = DemandForecaster()
        self.forecast = forecaster.run()

        # 2. Resource allocation ------------------------------------------- #
        allocator = ResourceAllocator()
        self.plan = allocator.run(self.forecast)
        self.summary = allocator.summary(self.plan)

        # 3. Optimisation --------------------------------------------------- #
        optimizer = CapacityOptimizer()
        self.schedule = optimizer.run(self.plan, rolling_days=7)

        # 4. Dashboard ------------------------------------------------------ #
        dashboard = CapacityDashboard(
            self.forecast, self.plan, history=forecaster.history,
            summary=self.summary,
        )
        html_path = dashboard.run()

        # 5. Markdown report ----------------------------------------------- #
        report_path = self._write_report(html_path)

        logger.info("=" * 70)
        logger.info("ED CAPACITY PLANNING PIPELINE - COMPLETE")
        logger.info("Report: %s", report_path)
        logger.info("=" * 70)
        return {
            "forecast": self.forecast,
            "plan": self.plan,
            "schedule": self.schedule,
            "summary": self.summary,
            "report": report_path,
            "dashboard": html_path,
        }

    # ------------------------------------------------------------------ #
    def _write_report(self, html_path: str) -> str:
        s = self.summary
        plan = self.plan
        schedule = self.schedule
        assert plan is not None and schedule is not None

        start = plan.index.min().date()
        end = plan.index.max().date()
        alert_days = plan[plan["alert"] == "HIGH_DEMAND"]
        weekly_cost = float(schedule["daily_cost"].sum())

        # Peak-day snapshot.
        peak_day = plan["capacity_utilization"].idxmax()
        peak = plan.loc[peak_day]

        alert_rows = "\n".join(
            f"| {d.date()} | {d.day_name()} | {row.capacity_utilization:.0%} | "
            f"{int(row.total_beds_needed)} | {int(row.physicians_needed)} | "
            f"{int(row.nurses_needed)} |"
            for d, row in alert_days.head(15).iterrows()
        ) or "| _None_ | - | - | - | - | - |"

        sched_rows = "\n".join(
            f"| {d.date()} | {row.day_of_week} | {int(row.physicians_scheduled)} | "
            f"{int(row.nurses_scheduled)} | {int(row.techs_scheduled)} | "
            f"${row.daily_cost:,.0f} | {'yes' if row.coverage_ok else 'no'} |"
            for d, row in schedule.iterrows()
        )

        md = f"""# Emergency Department Capacity Planning Report

**Project:** CVD Risk Stratification & ED Capacity Planning
**Forecast window:** {start} to {end} ({s.get('days', len(plan))} days)
**Generated:** {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}

---

## 1. Executive Summary

This report converts a 60-day, CVD risk-stratified forecast of Emergency
Department (ED) demand into an actionable resource-allocation and staffing plan.
Demand is forecast per risk group (Low / Medium / High) using an **ensemble of
Facebook Prophet and SARIMA** models. Forecasts drive risk-based resource rules,
a **20% surge buffer**, capacity-utilisation monitoring, and a linear-programming
staffing optimiser.

| Metric | Value |
|---|---|
| Total forecasted ED visits | {s.get('total_forecast_visits', 0):,.0f} |
| High-demand days (>85% utilisation) | {s.get('high_demand_days', 0)} |
| Mean capacity utilisation | {s.get('mean_utilization', 0):.0%} |
| Peak capacity utilisation | {s.get('peak_utilization', 0):.0%} |
| Peak total beds required | {s.get('peak_total_beds', 0)} |
| Peak ICU beds required | {s.get('peak_icu_beds', 0)} |
| Peak physicians required | {s.get('peak_physicians', 0)} |
| Peak nurses required | {s.get('peak_nurses', 0)} |
| Optimised 7-day staffing cost | ${weekly_cost:,.0f} |

---

## 2. Methodology

### 2.1 Demand Forecasting
- **Data:** daily ED visit counts by CVD risk group from the feature store
  (`data/feature_store/ed_demand_timeseries.csv`), with risk labels sourced from
  the trained `RiskStratifier`.
- **Models:** Prophet (weekly + yearly seasonality, 95% intervals) and SARIMA
  `(1,1,1)(1,1,1)_7`. The two are combined as a weighted **ensemble**
  (60% Prophet / 40% SARIMA). SARIMA also serves as the fallback if Prophet is
  unavailable.
- **Training window:** trailing {common.TRAIN_WINDOW_MONTHS} months;
  **horizon:** {common.FORECAST_HORIZON_DAYS} days.

### 2.2 Resource Allocation Rules
| Risk group | Intensity | Bed type | Key resources |
|---|---|---|---|
| High | 3 | ICU bed | Cardiologist consult, echo, troponin labs |
| Medium | 2 | Monitored bed | ECG, basic labs |
| Low | 1 | Standard ED bay | Basic triage |

A **20% surge buffer** is applied to every bed, staff, equipment and lab
estimate. Days with capacity utilisation above **85%** are flagged
`HIGH_DEMAND`.

### 2.3 Optimisation
A linear program (PuLP / CBC) minimises daily staffing cost subject to demand
coverage, minimum staffing floors, and surge-adjusted headcount ceilings, over a
rolling 7-day horizon.

---

## 3. Peak-Day Snapshot ({peak_day.date()}, {peak_day.day_name()})

- Capacity utilisation: **{peak['capacity_utilization']:.0%}**
- Total beds: **{int(peak['total_beds_needed'])}**
  (ICU {int(peak['icu_beds_needed'])}, monitored {int(peak['monitored_beds_needed'])},
  standard {int(peak['standard_beds_needed'])})
- Staff: **{int(peak['physicians_needed'])}** physicians,
  **{int(peak['nurses_needed'])}** nurses, **{int(peak['techs_needed'])}** techs
- Equipment: {int(peak['ecg_needed'])} ECG, {int(peak['echo_needed'])} echo;
  labs: {int(peak['lab_draws_needed'])} draws

---

## 4. High-Demand Alert Days (first 15)

| Date | Day | Utilisation | Beds | Physicians | Nurses |
|---|---|---|---|---|---|
{alert_rows}

---

## 5. Optimised 7-Day Staffing Schedule

| Date | Day | Physicians | Nurses | Techs | Daily cost | Coverage OK |
|---|---|---|---|---|---|---|
{sched_rows}

**Total optimised weekly staffing cost: ${weekly_cost:,.0f}**

---

## 6. Outputs

| Artifact | Path |
|---|---|
| Demand forecast (CSV) | `outputs/reports/ed_demand_forecast.csv` |
| Resource allocation plan (CSV) | `outputs/reports/resource_allocation_plan.csv` |
| Optimisation schedule (CSV) | `outputs/reports/capacity_optimization.csv` |
| Interactive dashboard (HTML) | `{os.path.relpath(html_path, common.PROJECT_ROOT)}` |
| Forecast & dashboard figures | `outputs/figures/ed_capacity_*.png`, `outputs/figures/ed_demand_forecast_*.png` |

---

## 7. Recommendations

1. **Pre-position ICU capacity and cardiology on flagged HIGH_DEMAND days** -
   these concentrate the High-risk (intensity-3) load.
2. **Adopt the optimised staffing schedule** to meet coverage at minimum cost;
   revisit weekly as new actuals arrive.
3. **Maintain the 20% surge buffer** as a floor; widen it ahead of seasonal
   peaks visible in the weekly-pattern figure.
4. **Refresh forecasts weekly** and monitor forecast-vs-actual drift to keep the
   Prophet/SARIMA ensemble calibrated.
"""
        common.ensure_dirs()
        path = os.path.join(common.REPORTS_DIR, "ed_capacity_report.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(md)
        logger.info("Saved ED capacity report -> %s", path)
        return path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    EDPipeline().run()


if __name__ == "__main__":
    main()
