"""
Weekly outpatient load forecaster.

Forecasts total weekly outpatient visit volume (and per-clinic volume) over a
configurable horizon using an ensemble of Facebook Prophet and SARIMA, with a
weighted average and automatic fallback to SARIMA-only (or a seasonal-naive
model) when Prophet is unavailable.
"""
from __future__ import annotations

import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.forecasting.common import CLINICS, forecast_config, load_weekly_demand
from src.utils.common import get_logger, get_paths

warnings.filterwarnings("ignore")
logger = get_logger("load_forecaster")

try:
    from prophet import Prophet
    _HAS_PROPHET = True
except Exception:  # pragma: no cover
    _HAS_PROPHET = False

try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    _HAS_SARIMA = True
except Exception:  # pragma: no cover
    _HAS_SARIMA = False


class LoadForecaster:
    def __init__(self):
        self.cfg = forecast_config()
        self.paths = get_paths()
        self.horizon = self.cfg["horizon_weeks"]
        self.wp = self.cfg["prophet_weight"]
        self.ws = self.cfg["sarima_weight"]

    # ---------- individual models ----------
    def _prophet(self, s: pd.Series, future_idx: pd.DatetimeIndex) -> np.ndarray | None:
        if not _HAS_PROPHET:
            return None
        try:
            dfp = pd.DataFrame({"ds": s.index, "y": s.values})
            m = Prophet(weekly_seasonality=False, daily_seasonality=False,
                        yearly_seasonality=True, interval_width=self.cfg["confidence_interval"])
            m.fit(dfp)
            fut = pd.DataFrame({"ds": future_idx})
            fc = m.predict(fut)
            return fc["yhat"].clip(lower=0).values
        except Exception as exc:  # pragma: no cover
            logger.warning("Prophet failed: %s", exc)
            return None

    def _sarima(self, s: pd.Series) -> np.ndarray | None:
        if not _HAS_SARIMA:
            return None
        try:
            order = tuple(self.cfg["sarima_order"])
            seas = tuple(self.cfg["sarima_seasonal_order"])
            # reduce seasonal period if series too short
            if len(s) < 2 * seas[3]:
                seas = (seas[0], seas[1], seas[2], min(12, max(4, len(s) // 3)))
            model = SARIMAX(s.values, order=order, seasonal_order=seas,
                            enforce_stationarity=False, enforce_invertibility=False)
            res = model.fit(disp=False)
            fc = res.forecast(steps=self.horizon)
            return np.clip(fc, 0, None)
        except Exception as exc:  # pragma: no cover
            logger.warning("SARIMA failed: %s", exc)
            return None

    @staticmethod
    def _seasonal_naive(s: pd.Series, horizon: int, period: int = 52) -> np.ndarray:
        period = min(period, len(s))
        tail = s.values[-period:]
        reps = int(np.ceil(horizon / period))
        return np.tile(tail, reps)[:horizon]

    # ---------- ensemble ----------
    def forecast_series(self, s: pd.Series, label: str) -> pd.DataFrame:
        last = s.index.max()
        future_idx = pd.date_range(last + pd.Timedelta(weeks=1), periods=self.horizon, freq="W-MON")
        p = self._prophet(s, future_idx)
        a = self._sarima(s)

        if p is not None and a is not None:
            yhat = self.wp * p + self.ws * a
            method = "ensemble(prophet+sarima)"
        elif a is not None:
            yhat = a
            method = "sarima"
        elif p is not None:
            yhat = p
            method = "prophet"
        else:
            yhat = self._seasonal_naive(s, self.horizon)
            method = "seasonal_naive"

        resid_sd = float(np.std(s.values[-26:])) if len(s) >= 8 else float(np.std(s.values))
        z = 1.96
        out = pd.DataFrame({
            "week": future_idx,
            "series": label,
            "forecast": np.round(yhat, 1),
            "lower": np.round(np.clip(yhat - z * resid_sd, 0, None), 1),
            "upper": np.round(yhat + z * resid_sd, 1),
            "method": method,
        })
        return out

    def run(self) -> pd.DataFrame:
        logger.info("=== Load forecasting start (horizon=%d weeks) ===", self.horizon)
        wk = load_weekly_demand().set_index("week")
        results = [self.forecast_series(wk["total_visits"], "total")]
        for c in CLINICS:
            if c in wk:
                results.append(self.forecast_series(wk[c], c))
        forecast = pd.concat(results, ignore_index=True)
        forecast.to_csv(self.paths["reports"] / "load_forecast.csv", index=False)
        self._plot(wk, forecast)
        logger.info("Forecast method (total): %s | mean weekly=%.1f",
                    forecast[forecast.series == "total"]["method"].iloc[0],
                    forecast[forecast.series == "total"]["forecast"].mean())
        logger.info("=== Load forecasting done ===")
        return forecast

    def _plot(self, wk: pd.DataFrame, forecast: pd.DataFrame):
        tot = forecast[forecast.series == "total"]
        fig, ax = plt.subplots(figsize=(15, 7))
        ax.plot(wk.index, wk["total_visits"], color="#3A6EA5", label="History")
        ax.plot(tot["week"], tot["forecast"], color="#D1495B", lw=2.5, label="Forecast")
        ax.fill_between(tot["week"], tot["lower"], tot["upper"], color="#D1495B",
                        alpha=0.2, label="95% CI")
        ax.set_title("Weekly outpatient visit volume — history & forecast",
                     fontsize=15, weight="bold")
        ax.set_ylabel("Visits / week")
        ax.legend()
        fig.savefig(self.paths["figures"] / "forecast_total_visits.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

        # per-clinic small multiples
        clinics = [c for c in CLINICS if c in wk]
        fig, axes = plt.subplots(len(clinics), 1, figsize=(14, 3 * len(clinics)), sharex=True)
        for ax, c in zip(np.atleast_1d(axes), clinics):
            sub = forecast[forecast.series == c]
            ax.plot(wk.index, wk[c], color="#3A6EA5")
            ax.plot(sub["week"], sub["forecast"], color="#E08E45", lw=2)
            ax.fill_between(sub["week"], sub["lower"], sub["upper"], color="#E08E45", alpha=0.2)
            ax.set_title(c)
        fig.suptitle("Per-clinic weekly load forecast", fontsize=15, weight="bold")
        fig.savefig(self.paths["figures"] / "forecast_by_clinic.png", dpi=300, bbox_inches="tight")
        plt.close(fig)


if __name__ == "__main__":
    LoadForecaster().run()
