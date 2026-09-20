"""Translate forecasted ED demand into daily resource requirements.

The :class:`ResourceAllocator` converts the per-risk-group demand forecast from
:class:`~src.ed_capacity.demand_forecaster.DemandForecaster` into concrete
staffing, bed, equipment, and lab requirements using risk-stratified rules,
applies a configurable surge buffer (default 20%), computes capacity
utilisation against a fixed department capacity, and flags HIGH_DEMAND days.

Allocation rules (per patient, per day)
--------------------------------------
* **High risk**   (intensity 3): ICU bed, cardiologist consult, echo, troponin.
* **Medium risk** (intensity 2): monitored bed, ECG, basic labs.
* **Low risk**    (intensity 1): standard ED bay, basic triage.

Output
------
``outputs/reports/resource_allocation_plan.csv``
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd

from . import common

logger = logging.getLogger(__name__)


@dataclass
class CapacityConfig:
    """Fixed department capacity and productivity assumptions."""

    surge_buffer: float = 0.20            # 20% surge buffer on all estimates
    utilization_alert: float = 0.85       # flag days above 85% utilisation

    # Bed capacity (total available beds per type). Sized for a mid-size
    # community ED cardiac unit handling ~13-16 CVD-related visits/day.
    icu_beds: int = 9
    monitored_beds: int = 4
    standard_beds: int = 5

    # Staff productivity: patients handled per shift by one staff member.
    patients_per_physician: float = 12.0
    patients_per_nurse: float = 5.0
    patients_per_tech: float = 15.0

    # Available staff (per day, across shifts).
    physicians_available: int = 5
    nurses_available: int = 10
    techs_available: int = 4

    # Equipment units available.
    ecg_machines: int = 3
    echo_units: int = 2
    # Lab processing capacity (draws/day).
    lab_capacity: int = 25

    # Length-of-stay driven bed-day factor (avg fraction of a day a bed is held).
    los_factor: Dict[str, float] = field(
        default_factory=lambda: {"Low": 0.25, "Medium": 0.5, "High": 1.0}
    )


class ResourceAllocator:
    """Compute daily resource needs, surge buffers, utilisation and alerts."""

    def __init__(self, config: CapacityConfig | None = None):
        self.config = config or CapacityConfig()
        common.ensure_dirs()

    # ------------------------------------------------------------------ #
    def _pivot_forecast(self, forecast: pd.DataFrame) -> pd.DataFrame:
        """Pivot tidy forecast to wide (date x risk group) demand counts."""
        wide = forecast.pivot_table(
            index="date", columns="risk_level", values="forecast", aggfunc="sum"
        )
        wide = wide.reindex(columns=common.RISK_ORDER).fillna(0.0)
        return wide

    def allocate(self, forecast: pd.DataFrame) -> pd.DataFrame:
        """Return a daily resource allocation plan from a demand forecast.

        Parameters
        ----------
        forecast:
            Tidy frame from :meth:`DemandForecaster.forecast_all` with columns
            ``date``, ``risk_level``, ``forecast``.
        """
        cfg = self.config
        demand = self._pivot_forecast(forecast)
        low, med, high = demand["Low"], demand["Medium"], demand["High"]
        total = low + med + high

        buf = 1.0 + cfg.surge_buffer
        plan = pd.DataFrame(index=demand.index)
        plan.index.name = "date"

        # --- Demand by group ------------------------------------------------ #
        plan["demand_low"] = low.round(1)
        plan["demand_medium"] = med.round(1)
        plan["demand_high"] = high.round(1)
        plan["demand_total"] = total.round(1)

        # --- Beds (with LOS factor + surge buffer) -------------------------- #
        icu = high * cfg.los_factor["High"]
        monitored = med * cfg.los_factor["Medium"]
        standard = low * cfg.los_factor["Low"]
        plan["icu_beds_needed"] = np.ceil(icu * buf).astype(int)
        plan["monitored_beds_needed"] = np.ceil(monitored * buf).astype(int)
        plan["standard_beds_needed"] = np.ceil(standard * buf).astype(int)
        plan["total_beds_needed"] = (
            plan["icu_beds_needed"]
            + plan["monitored_beds_needed"]
            + plan["standard_beds_needed"]
        )

        # --- Weighted acuity load (resource-intensity units) ---------------- #
        acuity_load = (
            low * common.RESOURCE_INTENSITY["Low"]
            + med * common.RESOURCE_INTENSITY["Medium"]
            + high * common.RESOURCE_INTENSITY["High"]
        )
        plan["acuity_load"] = acuity_load.round(1)

        # --- Staff (driven by acuity-weighted demand + surge buffer) -------- #
        plan["physicians_needed"] = np.ceil(
            acuity_load / cfg.patients_per_physician * buf
        ).astype(int)
        plan["nurses_needed"] = np.ceil(
            acuity_load / cfg.patients_per_nurse * buf
        ).astype(int)
        plan["techs_needed"] = np.ceil(
            total / cfg.patients_per_tech * buf
        ).astype(int)

        # --- Equipment ------------------------------------------------------ #
        # ECG for every Medium & High patient; echo for High only.
        plan["ecg_needed"] = np.ceil((med + high) / 8.0 * buf).astype(int)
        plan["echo_needed"] = np.ceil(high / 6.0 * buf).astype(int)

        # --- Labs (blood draws): High=2, Medium=1, Low=0 draws -------------- #
        lab_draws = high * 2 + med * 1
        plan["lab_draws_needed"] = np.ceil(lab_draws * buf).astype(int)

        # --- Capacity utilisation (max over resource classes) --------------- #
        util = pd.DataFrame(index=demand.index)
        util["beds"] = plan["total_beds_needed"] / (
            cfg.icu_beds + cfg.monitored_beds + cfg.standard_beds
        )
        util["icu"] = plan["icu_beds_needed"] / cfg.icu_beds
        util["physicians"] = plan["physicians_needed"] / cfg.physicians_available
        util["nurses"] = plan["nurses_needed"] / cfg.nurses_available
        util["labs"] = plan["lab_draws_needed"] / cfg.lab_capacity
        plan["bed_utilization"] = util["beds"].round(3)
        plan["icu_utilization"] = util["icu"].round(3)
        plan["staff_utilization"] = util[["physicians", "nurses"]].max(axis=1).round(3)
        plan["capacity_utilization"] = util.max(axis=1).round(3)

        # --- Alert system --------------------------------------------------- #
        plan["alert"] = np.where(
            plan["capacity_utilization"] > cfg.utilization_alert,
            "HIGH_DEMAND",
            "NORMAL",
        )
        plan["day_of_week"] = plan.index.day_name()
        plan["is_weekend"] = plan.index.dayofweek >= 5

        n_alerts = int((plan["alert"] == "HIGH_DEMAND").sum())
        logger.info(
            "Allocation plan built for %d days; %d HIGH_DEMAND days flagged.",
            len(plan), n_alerts,
        )
        self.plan_ = plan
        return plan

    def save_plan(self, plan: pd.DataFrame) -> str:
        common.ensure_dirs()
        path = os.path.join(common.REPORTS_DIR, "resource_allocation_plan.csv")
        plan.reset_index().to_csv(path, index=False)
        logger.info("Saved resource allocation plan -> %s", path)
        return path

    def summary(self, plan: pd.DataFrame) -> Dict[str, float]:
        """Return headline aggregates used by the report / dashboard."""
        return {
            "days": int(len(plan)),
            "high_demand_days": int((plan["alert"] == "HIGH_DEMAND").sum()),
            "peak_total_beds": int(plan["total_beds_needed"].max()),
            "peak_icu_beds": int(plan["icu_beds_needed"].max()),
            "peak_physicians": int(plan["physicians_needed"].max()),
            "peak_nurses": int(plan["nurses_needed"].max()),
            "mean_utilization": float(plan["capacity_utilization"].mean()),
            "peak_utilization": float(plan["capacity_utilization"].max()),
            "total_forecast_visits": float(plan["demand_total"].sum()),
        }

    def run(self, forecast: pd.DataFrame) -> pd.DataFrame:
        plan = self.allocate(forecast)
        self.save_plan(plan)
        return plan


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    from .demand_forecaster import DemandForecaster

    fc = DemandForecaster().run()
    ResourceAllocator().run(fc)


if __name__ == "__main__":
    main()
