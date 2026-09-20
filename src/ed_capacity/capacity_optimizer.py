"""Cost-minimising resource scheduling via linear programming.

The :class:`CapacityOptimizer` builds a small LP for each day of a rolling
7-day window that decides how many physicians, nurses and technicians to
schedule so that total staffing cost is minimised while:

* covering the acuity-weighted patient demand (coverage constraint),
* respecting a minimum staffing floor (safety constraint),
* staying within the available surge-adjusted headcount (capacity constraint).

PuLP (CBC solver) is used when available; a deterministic closed-form fallback
is provided so the module always produces a schedule.

Output
------
``outputs/reports/capacity_optimization.csv``
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from . import common
from .resource_allocator import CapacityConfig

logger = logging.getLogger(__name__)

try:
    import pulp

    _HAS_PULP = True
except Exception:  # pragma: no cover
    _HAS_PULP = False


@dataclass
class StaffCost:
    """Per-shift staffing cost (USD) and minimum staffing floors."""

    physician_cost: float = 2400.0
    nurse_cost: float = 1200.0
    tech_cost: float = 700.0
    min_physicians: int = 3
    min_nurses: int = 8
    min_techs: int = 2


class CapacityOptimizer:
    """Optimise staff scheduling to minimise cost subject to coverage limits."""

    def __init__(
        self,
        cost: StaffCost | None = None,
        capacity: CapacityConfig | None = None,
    ):
        self.cost = cost or StaffCost()
        self.capacity = capacity or CapacityConfig()
        common.ensure_dirs()

    # ------------------------------------------------------------------ #
    def _solve_day_lp(self, row: pd.Series) -> Dict[str, float]:
        """Solve the single-day staffing LP; fall back to closed form."""
        cfg, cost = self.capacity, self.cost
        # Required staff (from allocation) become coverage lower bounds.
        req_phys = float(row["physicians_needed"])
        req_nurse = float(row["nurses_needed"])
        req_tech = float(row["techs_needed"])

        # Surge-adjusted upper bounds on how many we can roster.
        max_phys = cfg.physicians_available
        max_nurse = cfg.nurses_available
        max_tech = cfg.techs_available

        if _HAS_PULP:
            try:
                return self._pulp_solve(
                    req_phys, req_nurse, req_tech, max_phys, max_nurse, max_tech
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("PuLP solve failed (%s); using fallback.", exc)

        # Closed-form fallback: clip demand to [floor, capacity].
        phys = int(min(max_phys, max(cost.min_physicians, np.ceil(req_phys))))
        nurse = int(min(max_nurse, max(cost.min_nurses, np.ceil(req_nurse))))
        tech = int(min(max_tech, max(cost.min_techs, np.ceil(req_tech))))
        return self._package(phys, nurse, tech, feasible=(
            phys >= req_phys and nurse >= req_nurse and tech >= req_tech))

    def _pulp_solve(
        self, req_phys, req_nurse, req_tech, max_phys, max_nurse, max_tech
    ) -> Dict[str, float]:
        cost = self.cost
        prob = pulp.LpProblem("daily_staffing", pulp.LpMinimize)
        p = pulp.LpVariable("physicians", lowBound=cost.min_physicians,
                            upBound=max_phys, cat="Integer")
        n = pulp.LpVariable("nurses", lowBound=cost.min_nurses,
                            upBound=max_nurse, cat="Integer")
        t = pulp.LpVariable("techs", lowBound=cost.min_techs,
                            upBound=max_tech, cat="Integer")

        # Objective: minimise staffing cost.
        prob += cost.physician_cost * p + cost.nurse_cost * n + cost.tech_cost * t

        # Coverage constraints (demand must be met where capacity allows).
        prob += p >= min(req_phys, max_phys)
        prob += n >= min(req_nurse, max_nurse)
        prob += t >= min(req_tech, max_tech)

        prob.solve(pulp.PULP_CBC_CMD(msg=0))
        status = pulp.LpStatus[prob.status]
        phys = int(round(p.value())) if p.value() is not None else cost.min_physicians
        nurse = int(round(n.value())) if n.value() is not None else cost.min_nurses
        tech = int(round(t.value())) if t.value() is not None else cost.min_techs
        feasible = (status == "Optimal" and phys >= req_phys
                    and nurse >= req_nurse and tech >= req_tech)
        return self._package(phys, nurse, tech, feasible=feasible, status=status)

    def _package(self, phys, nurse, tech, feasible=True, status="Fallback") -> Dict:
        cost = self.cost
        total = (cost.physician_cost * phys + cost.nurse_cost * nurse
                 + cost.tech_cost * tech)
        return {
            "physicians_scheduled": phys,
            "nurses_scheduled": nurse,
            "techs_scheduled": tech,
            "daily_cost": round(total, 2),
            "solver_status": status,
            "coverage_ok": bool(feasible),
        }

    # ------------------------------------------------------------------ #
    def optimize(self, plan: pd.DataFrame, rolling_days: int = 7) -> pd.DataFrame:
        """Optimise the first ``rolling_days`` of the allocation plan."""
        window = plan.head(rolling_days).copy()
        records: List[Dict] = []
        for date, row in window.iterrows():
            sol = self._solve_day_lp(row)
            sol["date"] = date
            sol["day_of_week"] = date.day_name()
            sol["demand_total"] = float(row["demand_total"])
            sol["physicians_required"] = int(row["physicians_needed"])
            sol["nurses_required"] = int(row["nurses_needed"])
            sol["techs_required"] = int(row["techs_needed"])
            records.append(sol)

        result = pd.DataFrame(records).set_index("date")
        cols = [
            "day_of_week", "demand_total",
            "physicians_required", "physicians_scheduled",
            "nurses_required", "nurses_scheduled",
            "techs_required", "techs_scheduled",
            "daily_cost", "coverage_ok", "solver_status",
        ]
        result = result[cols]
        self.schedule_ = result
        total_cost = result["daily_cost"].sum()
        logger.info(
            "Optimised %d-day schedule (solver=%s); weekly cost = $%s",
            rolling_days,
            "PuLP/CBC" if _HAS_PULP else "fallback",
            f"{total_cost:,.0f}",
        )
        return result

    def save(self, schedule: pd.DataFrame) -> str:
        common.ensure_dirs()
        path = os.path.join(common.REPORTS_DIR, "capacity_optimization.csv")
        schedule.reset_index().to_csv(path, index=False)
        logger.info("Saved optimisation schedule -> %s", path)
        return path

    def run(self, plan: pd.DataFrame, rolling_days: int = 7) -> pd.DataFrame:
        schedule = self.optimize(plan, rolling_days=rolling_days)
        self.save(schedule)
        return schedule


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    from .demand_forecaster import DemandForecaster
    from .resource_allocator import ResourceAllocator

    fc = DemandForecaster().run()
    plan = ResourceAllocator().run(fc)
    CapacityOptimizer().run(plan)


if __name__ == "__main__":
    main()
