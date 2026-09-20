"""
Schedule optimizer.

Given the weekly load forecast, solves a small linear program (via PuLP) that
minimises weekly staffing cost subject to meeting the surge-buffered demand for
each staff role. Falls back to the closed-form ceiling solution when PuLP is not
available (the LP and the ceiling solution coincide for this problem, but the LP
formulation is retained to demonstrate the optimisation layer and to allow easy
extension with additional constraints such as minimum staffing floors).
"""
from __future__ import annotations

import math

import pandas as pd

from src.forecasting.common import scheduling_config
from src.utils.common import get_logger, get_paths

logger = get_logger("schedule_optimizer")

try:
    import pulp
    _HAS_PULP = True
except Exception:  # pragma: no cover
    _HAS_PULP = False


class ScheduleOptimizer:
    def __init__(self):
        self.cfg = scheduling_config()
        self.paths = get_paths()
        self.buffer = float(self.cfg["surge_buffer"])
        self.vpp = float(self.cfg["visits_per_physician_per_week"])
        self.vpn = float(self.cfg["visits_per_nurse_per_week"])
        self.vpa = float(self.cfg["visits_per_admin_per_week"])
        self.cost = self.cfg["cost_per_week"]

    def _load_forecast(self) -> pd.DataFrame:
        fp = self.paths["reports"] / "load_forecast.csv"
        if not fp.exists():
            raise FileNotFoundError(f"{fp} not found. Run the load forecaster first.")
        df = pd.read_csv(fp)
        return df[df["series"] == "total"].reset_index(drop=True)

    def _solve_week(self, demand: float) -> dict:
        required = demand * (1.0 + self.buffer)
        roles = {
            "physicians": (self.vpp, float(self.cost["physician"])),
            "nurses": (self.vpn, float(self.cost["nurse"])),
            "admin_staff": (self.vpa, float(self.cost["admin"])),
        }
        if _HAS_PULP:
            prob = pulp.LpProblem("weekly_staffing", pulp.LpMinimize)
            vars = {r: pulp.LpVariable(r, lowBound=0, cat="Integer") for r in roles}
            prob += pulp.lpSum(vars[r] * roles[r][1] for r in roles)
            for r, (cap, _) in roles.items():
                prob += vars[r] * cap >= required, f"cover_{r}"
            prob.solve(pulp.PULP_CBC_CMD(msg=0))
            sol = {r: int(round(pulp.value(vars[r]))) for r in roles}
            status = pulp.LpStatus[prob.status]
        else:  # pragma: no cover
            sol = {r: math.ceil(required / cap) for r, (cap, _) in roles.items()}
            status = "CeilingFallback"

        weekly_cost = sum(sol[r] * roles[r][1] for r in roles)
        return {**sol, "weekly_cost": round(weekly_cost, 2), "solver_status": status}

    def optimize(self) -> pd.DataFrame:
        fc = self._load_forecast()
        rows = []
        for _, r in fc.iterrows():
            demand = float(r["forecast"])
            sol = self._solve_week(demand)
            rows.append({
                "week": r["week"],
                "forecast_visits": round(demand, 1),
                "required_capacity_visits": round(demand * (1.0 + self.buffer), 1),
                **sol,
            })
        return pd.DataFrame(rows)

    def run(self) -> pd.DataFrame:
        logger.info("=== Schedule optimization start (solver=%s) ===",
                    "PuLP/CBC" if _HAS_PULP else "ceiling-fallback")
        opt = self.optimize()
        out = self.paths["reports"] / "schedule_optimization.csv"
        opt.to_csv(out, index=False)
        logger.info("Total 12-week staffing cost: $%s | mean weekly cost: $%s",
                    f"{opt['weekly_cost'].sum():,.0f}", f"{opt['weekly_cost'].mean():,.0f}")
        logger.info("Saved %s", out)
        logger.info("=== Schedule optimization done ===")
        return opt


if __name__ == "__main__":
    ScheduleOptimizer().run()
