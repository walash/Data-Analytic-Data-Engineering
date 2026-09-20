"""
Resource scheduler.

Translates the weekly outpatient load forecast into staffing requirements
(physicians, nurses, administrative staff) using productivity ratios from the
project configuration. Adds a surge buffer, computes projected utilisation and
flags weeks that exceed the high-load utilisation threshold.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from src.forecasting.common import scheduling_config
from src.utils.common import get_logger, get_paths

logger = get_logger("resource_scheduler")


class ResourceScheduler:
    def __init__(self):
        self.cfg = scheduling_config()
        self.paths = get_paths()
        self.buffer = float(self.cfg["surge_buffer"])
        self.threshold = float(self.cfg["high_load_utilization_threshold"])
        self.vpp = float(self.cfg["visits_per_physician_per_week"])
        self.vpn = float(self.cfg["visits_per_nurse_per_week"])
        self.vpa = float(self.cfg["visits_per_admin_per_week"])

    def _load_forecast(self) -> pd.DataFrame:
        fp = self.paths["reports"] / "load_forecast.csv"
        if not fp.exists():
            raise FileNotFoundError(f"{fp} not found. Run the load forecaster first.")
        df = pd.read_csv(fp)
        return df[df["series"] == "total"].reset_index(drop=True)

    def build_schedule(self) -> pd.DataFrame:
        fc = self._load_forecast()
        rows = []
        for _, r in fc.iterrows():
            demand = float(r["forecast"])
            planned = demand * (1.0 + self.buffer)  # capacity to provision (with surge buffer)

            physicians = math.ceil(planned / self.vpp)
            nurses = math.ceil(planned / self.vpn)
            admin = math.ceil(planned / self.vpa)

            # Effective capacity given the (integer) staff provisioned
            phys_capacity = physicians * self.vpp
            util = demand / phys_capacity if phys_capacity else np.nan

            rows.append({
                "week": r["week"],
                "forecast_visits": round(demand, 1),
                "planned_capacity_visits": round(planned, 1),
                "physicians": physicians,
                "nurses": nurses,
                "admin_staff": admin,
                "total_staff": physicians + nurses + admin,
                "physician_capacity_visits": round(phys_capacity, 1),
                "projected_utilization": round(util, 3),
                "high_load_flag": bool(util >= self.threshold),
            })
        sched = pd.DataFrame(rows)
        return sched

    def run(self) -> pd.DataFrame:
        logger.info("=== Resource scheduling start ===")
        sched = self.build_schedule()
        out = self.paths["reports"] / "resource_schedule.csv"
        sched.to_csv(out, index=False)
        n_high = int(sched["high_load_flag"].sum())
        logger.info("Weeks scheduled: %d | high-load weeks: %d | mean physicians/wk: %.1f",
                    len(sched), n_high, sched["physicians"].mean())
        logger.info("Saved %s", out)
        logger.info("=== Resource scheduling done ===")
        return sched


if __name__ == "__main__":
    ResourceScheduler().run()
