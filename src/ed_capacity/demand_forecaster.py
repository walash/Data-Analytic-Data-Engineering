"""Time-series forecasting of Emergency Department demand by CVD risk group.

The :class:`DemandForecaster` builds a per-risk-group daily forecast for the
next :data:`~src.ed_capacity.common.FORECAST_HORIZON_DAYS` days using:

* **Prophet** (Facebook Prophet) - additive model with weekly/yearly
  seasonality, trained on the trailing ``TRAIN_WINDOW_MONTHS`` window.
* **SARIMA** (statsmodels) - seasonal ARIMA used both as a fallback when
  Prophet is unavailable and as the second ensemble member.
* **Ensemble** - a weighted average of the two forecasters (default 60/40 in
  favour of Prophet), which is the value fed to the resource allocator.

Outputs
-------
* ``outputs/reports/ed_demand_forecast.csv`` - tidy forecast with 95% CIs.
* ``outputs/figures/ed_demand_forecast_<group>.png`` - per-group forecast plot.
"""
from __future__ import annotations

import logging
import os
import warnings
from typing import Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import common  # noqa: E402

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

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


class DemandForecaster:
    """Forecast daily ED demand for each CVD risk group."""

    def __init__(
        self,
        horizon: int = common.FORECAST_HORIZON_DAYS,
        train_window_months: int = common.TRAIN_WINDOW_MONTHS,
        prophet_weight: float = 0.6,
    ):
        self.horizon = horizon
        self.train_window_months = train_window_months
        self.prophet_weight = prophet_weight
        self.sarima_weight = 1.0 - prophet_weight
        self.history: Optional[pd.DataFrame] = None
        self.forecasts: Dict[str, pd.DataFrame] = {}
        common.ensure_dirs()

    # ------------------------------------------------------------------ #
    # Data
    # ------------------------------------------------------------------ #
    def load_data(self) -> pd.DataFrame:
        """Load and cache the wide daily-demand history (date x risk group)."""
        self.history = common.wide_demand()
        logger.info(
            "Loaded demand history: %s -> %s (%d days)",
            self.history.index.min().date(),
            self.history.index.max().date(),
            len(self.history),
        )
        return self.history

    def _training_series(self, group: str) -> pd.Series:
        """Return the trailing training window for a single risk group."""
        if self.history is None:
            self.load_data()
        series = self.history[group].astype(float)
        cutoff = series.index.max() - pd.DateOffset(months=self.train_window_months)
        train = series[series.index >= cutoff]
        # Guard: keep at least a year of data if the window is too aggressive.
        if len(train) < 365:
            train = series
        return train

    # ------------------------------------------------------------------ #
    # Individual forecasters
    # ------------------------------------------------------------------ #
    def _forecast_prophet(self, train: pd.Series) -> Optional[pd.DataFrame]:
        if not _HAS_PROPHET:
            return None
        try:
            dfp = pd.DataFrame({"ds": train.index, "y": train.values})
            model = Prophet(
                weekly_seasonality=True,
                yearly_seasonality=True,
                daily_seasonality=False,
                interval_width=0.95,
                seasonality_mode="additive",
            )
            model.fit(dfp)
            future = model.make_future_dataframe(periods=self.horizon, freq="D")
            fc = model.predict(future).tail(self.horizon)
            out = pd.DataFrame(
                {
                    "ds": fc["ds"].values,
                    "yhat": np.clip(fc["yhat"].values, 0, None),
                    "yhat_lower": np.clip(fc["yhat_lower"].values, 0, None),
                    "yhat_upper": np.clip(fc["yhat_upper"].values, 0, None),
                }
            ).set_index("ds")
            return out
        except Exception as exc:  # pragma: no cover
            logger.warning("Prophet failed (%s).", exc)
            return None

    def _forecast_sarima(self, train: pd.Series) -> Optional[pd.DataFrame]:
        if not _HAS_SARIMA:
            return None
        try:
            model = SARIMAX(
                train.values,
                order=(1, 1, 1),
                seasonal_order=(1, 1, 1, 7),
                enforce_stationarity=False,
                enforce_invertibility=False,
            )
            res = model.fit(disp=False)
            pred = res.get_forecast(steps=self.horizon)
            mean = np.clip(pred.predicted_mean, 0, None)
            ci = pred.conf_int(alpha=0.05)
            ci = np.asarray(ci)
            idx = pd.date_range(
                train.index.max() + pd.Timedelta(days=1),
                periods=self.horizon,
                freq="D",
            )
            out = pd.DataFrame(
                {
                    "yhat": mean,
                    "yhat_lower": np.clip(ci[:, 0], 0, None),
                    "yhat_upper": np.clip(ci[:, 1], 0, None),
                },
                index=idx,
            )
            return out
        except Exception as exc:  # pragma: no cover
            logger.warning("SARIMA failed (%s).", exc)
            return None

    # ------------------------------------------------------------------ #
    # Ensemble
    # ------------------------------------------------------------------ #
    def forecast_group(self, group: str) -> pd.DataFrame:
        """Forecast one risk group; returns a frame with model + ensemble cols."""
        train = self._training_series(group)
        prophet_fc = self._forecast_prophet(train)
        sarima_fc = self._forecast_sarima(train)

        if prophet_fc is None and sarima_fc is None:
            raise RuntimeError(
                "Neither Prophet nor SARIMA is available - cannot forecast."
            )

        # Align on a common future index.
        if prophet_fc is not None:
            index = prophet_fc.index
        else:
            index = sarima_fc.index

        frame = pd.DataFrame(index=index)
        frame["risk_level"] = group

        pw, sw = self.prophet_weight, self.sarima_weight
        if prophet_fc is None:
            pw, sw = 0.0, 1.0
        elif sarima_fc is None:
            pw, sw = 1.0, 0.0

        if prophet_fc is not None:
            frame["prophet_yhat"] = prophet_fc["yhat"].reindex(index).values
        if sarima_fc is not None:
            frame["sarima_yhat"] = sarima_fc["yhat"].reindex(index).values

        p_mean = prophet_fc["yhat"].reindex(index).values if prophet_fc is not None else 0.0
        s_mean = sarima_fc["yhat"].reindex(index).values if sarima_fc is not None else 0.0
        frame["forecast"] = np.clip(pw * p_mean + sw * s_mean, 0, None)

        # Ensemble CI: weighted combination of the members' bounds.
        p_lo = prophet_fc["yhat_lower"].reindex(index).values if prophet_fc is not None else 0.0
        p_hi = prophet_fc["yhat_upper"].reindex(index).values if prophet_fc is not None else 0.0
        s_lo = sarima_fc["yhat_lower"].reindex(index).values if sarima_fc is not None else 0.0
        s_hi = sarima_fc["yhat_upper"].reindex(index).values if sarima_fc is not None else 0.0
        frame["forecast_lower"] = np.clip(pw * p_lo + sw * s_lo, 0, None)
        frame["forecast_upper"] = np.clip(pw * p_hi + sw * s_hi, 0, None)

        frame["model"] = (
            "ensemble" if prophet_fc is not None and sarima_fc is not None
            else ("prophet" if prophet_fc is not None else "sarima")
        )
        frame.index.name = "date"
        return frame

    def forecast_all(self) -> pd.DataFrame:
        """Forecast every risk group and return a tidy combined frame."""
        if self.history is None:
            self.load_data()
        parts = []
        for group in common.RISK_ORDER:
            logger.info("Forecasting demand for risk group: %s", group)
            fc = self.forecast_group(group)
            self.forecasts[group] = fc
            parts.append(fc.reset_index())
        combined = pd.concat(parts, ignore_index=True)
        combined = combined.sort_values(["risk_level", "date"]).reset_index(drop=True)
        return combined

    # ------------------------------------------------------------------ #
    # Persistence & plotting
    # ------------------------------------------------------------------ #
    def save_forecast(self, combined: pd.DataFrame) -> str:
        common.ensure_dirs()
        path = os.path.join(common.REPORTS_DIR, "ed_demand_forecast.csv")
        combined.to_csv(path, index=False)
        logger.info("Saved demand forecast -> %s", path)
        return path

    def plot_forecasts(self) -> Dict[str, str]:
        """Plot each group's history + forecast with 95% CI; return file paths."""
        if not self.forecasts:
            self.forecast_all()
        paths = {}
        for group, fc in self.forecasts.items():
            fig, ax = plt.subplots(figsize=(12, 5))
            hist = self.history[group].astype(float)
            hist_tail = hist.tail(180)
            ax.plot(hist_tail.index, hist_tail.values, color="#444444",
                    lw=1.2, label="History (last 180d)")
            ax.plot(fc.index, fc["forecast"], color=common.RISK_COLORS[group],
                    lw=2.0, label="Forecast (ensemble)")
            ax.fill_between(fc.index, fc["forecast_lower"], fc["forecast_upper"],
                            color=common.RISK_COLORS[group], alpha=0.2,
                            label="95% CI")
            ax.set_title(f"ED Demand Forecast - {group} Risk Group "
                         f"({self.horizon}-day horizon)")
            ax.set_xlabel("Date")
            ax.set_ylabel("Daily ED visits")
            ax.legend(loc="upper left")
            fig.autofmt_xdate()
            fig.tight_layout()
            path = os.path.join(common.FIGURES_DIR,
                                f"ed_demand_forecast_{group.lower()}.png")
            fig.savefig(path, dpi=200)
            plt.close(fig)
            paths[group] = path
            logger.info("Saved forecast figure -> %s", path)
        return paths

    def run(self) -> pd.DataFrame:
        """Full forecasting run: load -> forecast -> save -> plot."""
        self.load_data()
        combined = self.forecast_all()
        self.save_forecast(combined)
        self.plot_forecasts()
        return combined


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    DemandForecaster().run()


if __name__ == "__main__":
    main()
