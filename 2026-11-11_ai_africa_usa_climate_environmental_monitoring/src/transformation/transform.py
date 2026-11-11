"""Transformation module for the AI-Driven Climate & Environmental Monitoring pipeline.

Reads the five raw World Bank indicator CSVs from ``data/raw/``, cleans them,
builds a wide country-year dataset, engineers features and trains a per-country
scikit-learn ``LinearRegression`` model to forecast renewable energy share
for the configured forecast years (default 2025-2027).

Outputs written to ``data/processed/``:
    climate_indicators_long.csv  - cleaned, deduplicated long-format data
    climate_indicators_wide.csv  - one row per country-year, one column per
                                   indicator plus derived features
    country_profiles.csv         - one row per country with latest values
    ml_predictions.csv           - renewable energy forecasts per country/year

Usage:
    python src/transformation/transform.py
"""

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

DEFAULT_INDICATORS = {
    "AG.LND.FRST.ZS": {"file": "forest_area.csv",
                       "column": "forest_area_pct"},
    "EG.FEC.RNEW.ZS": {"file": "renewable_energy.csv",
                       "column": "renewable_energy_pct"},
    "AG.LND.AGRI.ZS": {"file": "agricultural_land.csv",
                       "column": "agricultural_land_pct"},
    "EG.USE.ELEC.KH.PC": {"file": "electric_power_consumption.csv",
                          "column": "electric_power_kwh_pc"},
    "EG.ELC.ACCS.ZS": {"file": "electricity_access.csv",
                       "column": "electricity_access_pct"},
}

# World Bank labels -> short, human-friendly names.
COUNTRY_NAME_MAP = {
    "Egypt, Arab Rep.": "Egypt",
    "Gambia, The": "Gambia",
    "Cabo Verde": "Cape Verde",
    "Cote d'Ivoire": "Côte d'Ivoire",
    "Tanzania, United Republic of": "Tanzania",
    "United States of America": "United States",
    "Congo, Dem. Rep.": "DR Congo",
}

# ISO-3 code -> (region, sub_region)
REGION_MAP = {
    "USA": ("USA", "North America"),
    "DZA": ("Africa", "North Africa"), "EGY": ("Africa", "North Africa"),
    "SDN": ("Africa", "North Africa"),
    "NGA": ("Africa", "West Africa"), "GHA": ("Africa", "West Africa"),
    "SEN": ("Africa", "West Africa"), "CIV": ("Africa", "West Africa"),
    "MLI": ("Africa", "West Africa"), "BFA": ("Africa", "West Africa"),
    "NER": ("Africa", "West Africa"), "BEN": ("Africa", "West Africa"),
    "TGO": ("Africa", "West Africa"), "GIN": ("Africa", "West Africa"),
    "SLE": ("Africa", "West Africa"), "LBR": ("Africa", "West Africa"),
    "GMB": ("Africa", "West Africa"), "GNB": ("Africa", "West Africa"),
    "CPV": ("Africa", "West Africa"),
    "KEN": ("Africa", "East Africa"), "ETH": ("Africa", "East Africa"),
    "TZA": ("Africa", "East Africa"), "UGA": ("Africa", "East Africa"),
    "RWA": ("Africa", "East Africa"), "MDG": ("Africa", "East Africa"),
    "CMR": ("Africa", "Central Africa"),
    "ZAF": ("Africa", "Southern Africa"), "MOZ": ("Africa", "Southern Africa"),
    "ZMB": ("Africa", "Southern Africa"), "ZWE": ("Africa", "Southern Africa"),
    "AGO": ("Africa", "Southern Africa"),
}

REQUIRED_COLUMNS = ["country_code", "country_name", "indicator_id",
                    "indicator_name", "year", "value"]

logger = logging.getLogger("transform")


def load_config(path=None):
    """Load the YAML configuration.

    Args:
        path: Optional explicit path; defaults to ``CONFIG_PATH`` env var or
            ``config/config.yaml``.

    Returns:
        dict: Parsed configuration (empty if the file is missing).
    """
    config_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        logger.warning("Config file %s not found; using defaults", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def setup_logging(config):
    """Configure logging using the ``logging`` section of the config.

    Args:
        config: Parsed configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get(
            "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ),
    )


def read_raw(raw_dir, indicators):
    """Read every raw indicator CSV into a single long DataFrame.

    Missing files are logged and skipped.

    Args:
        raw_dir: Directory containing the raw CSV files.
        indicators: Indicator configuration mapping.

    Returns:
        pandas.DataFrame: Concatenated raw records.

    Raises:
        FileNotFoundError: If none of the raw files exist.
    """
    frames = []
    for indicator_id, meta in indicators.items():
        path = Path(raw_dir) / meta["file"]
        if not path.exists():
            logger.warning("Raw file missing, skipping: %s", path)
            continue
        frame = pd.read_csv(path)
        missing = set(REQUIRED_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        frame["indicator_id"] = frame["indicator_id"].fillna(indicator_id)
        logger.info("Read %d rows from %s", len(frame), path.name)
        frames.append(frame[REQUIRED_COLUMNS])
    if not frames:
        raise FileNotFoundError(f"No raw indicator files found in {raw_dir}")
    return pd.concat(frames, ignore_index=True)


def normalize_country_name(name):
    """Return a short, normalised country name.

    Args:
        name: Raw World Bank country label.

    Returns:
        str: Normalised name (input stripped if no mapping exists).
    """
    if pd.isna(name):
        return name
    name = str(name).strip()
    return COUNTRY_NAME_MAP.get(name, name)


def clean(df):
    """Clean the long-format dataset.

    Steps: strip strings, cast ``year`` to int and ``value`` to float, drop
    rows with null keys or values, normalise country names, attach region
    metadata and deduplicate on (country_code, indicator_id, year) keeping
    the last occurrence.

    Args:
        df: Raw long-format DataFrame.

    Returns:
        pandas.DataFrame: Cleaned long-format DataFrame.
    """
    start = len(df)
    out = df.copy()
    for col in ["country_code", "country_name", "indicator_id",
                "indicator_name"]:
        out[col] = out[col].astype("string").str.strip()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["country_code", "indicator_id", "year", "value"])
    out["year"] = out["year"].astype(int)
    out["value"] = out["value"].astype(float)
    out["country_code"] = out["country_code"].str.upper()
    out["country_name"] = out["country_name"].map(normalize_country_name)

    regions = out["country_code"].map(REGION_MAP)
    out["region"] = regions.map(lambda r: r[0] if isinstance(r, tuple)
                                else "Other")
    out["sub_region"] = regions.map(lambda r: r[1] if isinstance(r, tuple)
                                    else "Other")

    out = out.drop_duplicates(subset=["country_code", "indicator_id", "year"],
                              keep="last")
    out = out.sort_values(["country_code", "indicator_id", "year"])
    logger.info("Cleaned long data: %d -> %d rows", start, len(out))
    return out.reset_index(drop=True)


def to_wide(long_df, indicators):
    """Pivot long data to one row per country-year with indicator columns.

    Args:
        long_df: Cleaned long-format DataFrame.
        indicators: Indicator configuration mapping (provides column names).

    Returns:
        pandas.DataFrame: Wide-format dataset.
    """
    column_map = {k: v["column"] for k, v in indicators.items()}
    wide = long_df.pivot_table(
        index=["country_code", "country_name", "region", "sub_region", "year"],
        columns="indicator_id", values="value", aggfunc="mean",
    ).reset_index()
    wide.columns.name = None
    wide = wide.rename(columns=column_map)
    for col in column_map.values():
        if col not in wide.columns:
            wide[col] = np.nan
    ordered = ["country_code", "country_name", "region", "sub_region", "year"]
    wide = wide[ordered + list(column_map.values())]
    return wide.sort_values(["country_code", "year"]).reset_index(drop=True)


def add_features(wide):
    """Add derived climate features to the wide dataset.

    Features:
        renewable_energy_trend: year-over-year change in renewable energy
            share (percentage points).
        forest_loss_rate: annual % loss of forest area relative to the
            previous year (positive = forest lost, negative = gain).
        electricity_access_change: YoY change in access (pp).
        agricultural_land_change: YoY change in agricultural land (pp).

    Args:
        wide: Wide-format DataFrame sorted by country and year.

    Returns:
        pandas.DataFrame: Copy with the additional feature columns.
    """
    out = wide.sort_values(["country_code", "year"]).copy()
    grouped = out.groupby("country_code", group_keys=False)

    def yoy(col):
        prev = grouped[col].shift(1)
        gap = out["year"] - grouped["year"].shift(1)
        return (out[col] - prev).where(gap == 1)

    out["renewable_energy_trend"] = yoy("renewable_energy_pct").round(4)
    prev_forest = grouped["forest_area_pct"].shift(1)
    gap = out["year"] - grouped["year"].shift(1)
    loss = (prev_forest - out["forest_area_pct"]) / prev_forest * 100
    out["forest_loss_rate"] = loss.where((gap == 1) & (prev_forest > 0)) \
        .round(4)
    out["electricity_access_change"] = yoy("electricity_access_pct").round(4)
    out["agricultural_land_change"] = yoy("agricultural_land_pct").round(4)
    logger.info("Added derived features to %d country-year rows", len(out))
    return out.reset_index(drop=True)


def build_country_profiles(wide):
    """Summarise each country with its latest non-null indicator values.

    Args:
        wide: Feature-enriched wide DataFrame.

    Returns:
        pandas.DataFrame: One row per country.
    """
    value_cols = ["forest_area_pct", "renewable_energy_pct",
                  "agricultural_land_pct", "electric_power_kwh_pc",
                  "electricity_access_pct"]
    rows = []
    for (code, name, region, sub_region), grp in wide.groupby(
            ["country_code", "country_name", "region", "sub_region"]):
        grp = grp.sort_values("year")
        row = {"country_code": code, "country_name": name, "region": region,
               "sub_region": sub_region,
               "first_year": int(grp["year"].min()),
               "last_year": int(grp["year"].max())}
        for col in value_cols:
            series = grp[["year", col]].dropna()
            if series.empty:
                row[f"latest_{col}"] = np.nan
                row[f"latest_{col}_year"] = np.nan
                row[f"change_{col}"] = np.nan
                continue
            row[f"latest_{col}"] = round(float(series[col].iloc[-1]), 4)
            row[f"latest_{col}_year"] = int(series["year"].iloc[-1])
            row[f"change_{col}"] = round(
                float(series[col].iloc[-1] - series[col].iloc[0]), 4)
        row["avg_forest_loss_rate"] = round(
            float(grp["forest_loss_rate"].mean()), 4) \
            if grp["forest_loss_rate"].notna().any() else np.nan
        rows.append(row)
    profiles = pd.DataFrame(rows)
    for col in [c for c in profiles.columns if c.endswith("_year")]:
        profiles[col] = profiles[col].astype("Int64")
    logger.info("Built %d country profiles", len(profiles))
    return profiles


def forecast_renewables(wide, forecast_years, min_points=3):
    """Forecast renewable energy share with a per-country LinearRegression.

    For every country a model ``renewable_energy_pct ~ year`` is fitted on
    all available historical observations. Predictions are clipped to the
    valid 0-100 % range. In-sample R² and MAE are reported for transparency.

    Args:
        wide: Wide DataFrame containing ``renewable_energy_pct``.
        forecast_years: Years to predict.
        min_points: Minimum observations required to fit a model.

    Returns:
        pandas.DataFrame: One row per country and forecast year.
    """
    rows = []
    for (code, name, region), grp in wide.groupby(
            ["country_code", "country_name", "region"]):
        hist = grp[["year", "renewable_energy_pct"]].dropna()
        if len(hist) < min_points:
            logger.warning("Skipping %s: only %d observations", code, len(hist))
            continue
        X = hist[["year"]].to_numpy(dtype=float)
        y = hist["renewable_energy_pct"].to_numpy(dtype=float)
        model = LinearRegression().fit(X, y)
        fitted = model.predict(X)
        r2 = r2_score(y, fitted) if np.var(y) > 0 else 1.0
        mae = mean_absolute_error(y, fitted)
        preds = model.predict(np.array(forecast_years, dtype=float)
                              .reshape(-1, 1))
        for year, pred in zip(forecast_years, preds):
            rows.append({
                "country_code": code,
                "country_name": name,
                "region": region,
                "forecast_year": int(year),
                "predicted_renewable_energy_pct": round(
                    float(np.clip(pred, 0, 100)), 4),
                "last_actual_year": int(hist["year"].max()),
                "last_actual_value": round(float(y[hist["year"].argmax()]), 4),
                "trend_slope_pp_per_year": round(float(model.coef_[0]), 4),
                "r2_score": round(float(r2), 4),
                "mae": round(float(mae), 4),
                "training_points": int(len(hist)),
                "model_name": "LinearRegression",
            })
    preds_df = pd.DataFrame(rows)
    logger.info("Generated %d renewable-energy forecasts for %d countries",
                len(preds_df), preds_df["country_code"].nunique()
                if not preds_df.empty else 0)
    return preds_df


def transform(config):
    """Run the full transformation stage.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        dict: Mapping of output filename -> row count.
    """
    paths = config.get("paths", {})
    raw_dir = PROJECT_ROOT / paths.get("raw_data", "data/raw")
    out_dir = PROJECT_ROOT / paths.get("processed_data", "data/processed")
    indicators = config.get("indicators") or DEFAULT_INDICATORS
    ml_cfg = config.get("ml", {})
    forecast_years = ml_cfg.get("forecast_years", [2025, 2026, 2027])

    long_df = clean(read_raw(raw_dir, indicators))
    wide = add_features(to_wide(long_df, indicators))
    profiles = build_country_profiles(wide)
    predictions = forecast_renewables(
        wide, forecast_years, ml_cfg.get("min_training_points", 3))

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "climate_indicators_long.csv": long_df,
        "climate_indicators_wide.csv": wide,
        "country_profiles.csv": profiles,
        "ml_predictions.csv": predictions,
    }
    summary = {}
    for filename, frame in outputs.items():
        path = out_dir / filename
        frame.to_csv(path, index=False)
        summary[filename] = len(frame)
        logger.info("Wrote %d rows to %s", len(frame), path)
    return summary


def main():
    """Command-line entry point.

    Returns:
        int: Process exit code.
    """
    config = load_config()
    setup_logging(config)
    try:
        summary = transform(config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1
    logger.info("Transformation complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
