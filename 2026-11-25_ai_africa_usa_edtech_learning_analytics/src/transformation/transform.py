"""Transformation module for the EdTech & Learning Analytics pipeline.

Reads the five World Bank indicator CSVs from ``data/raw/``, cleans them
(null handling, type casting, deduplication), merges them into a single
country-year dataset and derives an ``edtech_readiness_score``:

    score = weighted mean of the available components
            internet_users_pct        (weight 0.4)
            youth_literacy_rate       (weight 0.3)
            tertiary_enrollment_rate  (weight 0.3, capped at 100)

Indicators are reported irregularly, so the score for each country-year
uses the most recent value known up to that year (forward fill within a
country). Weights are re-normalised over the components that exist, and
``readiness_components`` records how many were available.

Outputs (in ``data/processed/``):
    raw_indicators_clean.csv  cleaned long-format observations
    education_analytics.csv   merged country-year dataset with score
    country_summary.csv       latest values, growth and rank per country
"""

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
CSV_COLUMNS = [
    "country_id", "country_name", "indicator_id",
    "indicator_name", "year", "value",
]
DEFAULT_WEIGHTS = {
    "internet_users_pct": 0.4,
    "youth_literacy_rate": 0.3,
    "tertiary_enrollment_rate": 0.3,
}

logger = logging.getLogger("transform")


def load_config(path=None):
    """Load the YAML configuration file.

    Args:
        path: Optional config path; defaults to ``CONFIG_PATH`` env var
            or ``config/config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_raw_files(raw_dir, indicators):
    """Read every configured indicator CSV into one long DataFrame.

    Args:
        raw_dir: Directory containing the raw CSV files.
        indicators: ``world_bank_api.indicators`` config list.

    Returns:
        pandas.DataFrame: Concatenated raw data with an extra
        ``indicator_key`` column holding the short indicator name.
    """
    frames = []
    for ind in indicators:
        path = raw_dir / ind["file"]
        if not path.exists():
            logger.warning("Missing raw file %s - skipping", path)
            continue
        df = pd.read_csv(path)
        missing = set(CSV_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"{path.name} is missing columns {missing}")
        df["indicator_key"] = ind["name"]
        frames.append(df)
        logger.info("Read %d rows from %s", len(df), path.name)
    if not frames:
        raise FileNotFoundError(f"No raw indicator files found in {raw_dir}")
    return pd.concat(frames, ignore_index=True)


def clean_data(df, region_map):
    """Clean raw observations.

    Steps: strip strings, cast ``year`` to int and ``value`` to float,
    drop rows with null or non-numeric values, drop exact and
    country/indicator/year duplicates (keeping the last), and tag each
    row with its region (Africa / USA).

    Args:
        df: Raw long-format DataFrame.
        region_map: Mapping of country code to region.

    Returns:
        pandas.DataFrame: Cleaned long-format DataFrame.
    """
    out = df.copy()
    for col in ["country_id", "country_name", "indicator_id",
                "indicator_name"]:
        out[col] = out[col].astype(str).str.strip()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    before = len(out)
    out = out.dropna(subset=["country_id", "year", "value"])
    out["year"] = out["year"].astype(int)
    out["value"] = out["value"].astype(float)
    out = out[out["value"] >= 0]
    out = out.drop_duplicates()
    out = out.drop_duplicates(
        subset=["country_id", "indicator_id", "year"], keep="last")
    out["region"] = out["country_id"].map(region_map).fillna("Other")
    logger.info("Cleaning removed %d of %d rows", before - len(out), before)
    return out.sort_values(
        ["country_id", "indicator_id", "year"]).reset_index(drop=True)


def compute_readiness(frame, weights):
    """Compute the weighted EdTech readiness score row by row.

    Args:
        frame: DataFrame containing the weighted component columns.
        weights: Mapping of component column to weight.

    Returns:
        tuple(pandas.Series, pandas.Series): score (0-100, rounded to
        2 dp) and number of components used.
    """
    comps = pd.DataFrame(
        {col: frame[col].clip(upper=100) for col in weights})
    weight_vec = pd.Series(weights)
    present = comps.notna()
    weighted_sum = (comps.fillna(0) * weight_vec).sum(axis=1)
    weight_total = (present * weight_vec).sum(axis=1)
    score = (weighted_sum / weight_total.replace(0, np.nan)).round(2)
    return score, present.sum(axis=1).astype(int)


def build_country_year(clean, weights):
    """Pivot cleaned data to one row per country-year and add metrics.

    Args:
        clean: Cleaned long-format DataFrame.
        weights: Readiness component weights.

    Returns:
        pandas.DataFrame: Merged country-year dataset.
    """
    wide = clean.pivot_table(
        index=["country_id", "country_name", "region", "year"],
        columns="indicator_key", values="value", aggfunc="last",
    ).reset_index()
    wide.columns.name = None
    for col in ["primary_completion_rate", "education_expenditure_pct_gdp",
                "tertiary_enrollment_rate", "internet_users_pct",
                "youth_literacy_rate"]:
        if col not in wide.columns:
            wide[col] = np.nan
    wide = wide.sort_values(["country_id", "year"]).reset_index(drop=True)

    filled = wide.groupby("country_id")[list(weights)].ffill()
    wide["edtech_readiness_score"], wide["readiness_components"] = (
        compute_readiness(filled, weights))
    wide["internet_users_yoy_change"] = (
        wide.groupby("country_id")["internet_users_pct"].diff().round(3))
    usa = wide[wide["country_id"] == "US"].set_index("year")[
        "internet_users_pct"]
    wide["digital_learning_gap_vs_usa"] = (
        wide["year"].map(usa) - wide["internet_users_pct"]).round(3)
    return wide


def build_country_summary(clean, country_year, weights):
    """Summarise each country using its latest available observations.

    Args:
        clean: Cleaned long-format DataFrame.
        country_year: Output of :func:`build_country_year`.
        weights: Readiness component weights.

    Returns:
        pandas.DataFrame: One row per country with latest values,
        latest years, internet growth, readiness score and rank.
    """
    latest = (clean.sort_values("year")
              .groupby(["country_id", "indicator_key"]).tail(1))
    values = latest.pivot(index="country_id", columns="indicator_key",
                          values="value")
    years = latest.pivot(index="country_id", columns="indicator_key",
                         values="year").add_suffix("_year")
    summary = values.join(years)
    summary.columns.name = None
    names = clean.groupby("country_id")[["country_name", "region"]].last()
    summary = names.join(summary).reset_index()
    for col in weights:
        if col not in summary.columns:
            summary[col] = np.nan

    inet = clean[clean["indicator_key"] == "internet_users_pct"]
    span = inet.sort_values("year").groupby("country_id")["value"].agg(
        ["first", "last"])
    summary["internet_growth_pp"] = summary["country_id"].map(
        (span["last"] - span["first"]).round(2))

    summary["edtech_readiness_score"], summary["readiness_components"] = (
        compute_readiness(summary, weights))
    summary["readiness_rank"] = (
        summary["edtech_readiness_score"]
        .rank(ascending=False, method="min").astype("Int64"))
    summary["observation_count"] = summary["country_id"].map(
        clean.groupby("country_id").size())
    summary["first_year"] = summary["country_id"].map(
        clean.groupby("country_id")["year"].min())
    summary["last_year"] = summary["country_id"].map(
        clean.groupby("country_id")["year"].max())
    return summary.sort_values("readiness_rank").reset_index(drop=True)


def transform(config=None):
    """Run the full transformation stage.

    Args:
        config: Configuration dict (loaded from disk if omitted).

    Returns:
        dict: Row counts for each output file.
    """
    config = config or load_config()
    api_cfg = config["world_bank_api"]
    raw_dir = PROJECT_ROOT / config["paths"]["raw_data_dir"]
    out_dir = PROJECT_ROOT / config["paths"]["processed_data_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = config.get("pipeline", {}).get(
        "readiness_weights", DEFAULT_WEIGHTS)
    region_map = {c["code"]: c["region"] for c in api_cfg["countries"]}

    clean = clean_data(read_raw_files(raw_dir, api_cfg["indicators"]),
                       region_map)
    country_year = build_country_year(clean, weights)
    summary = build_country_summary(clean, country_year, weights)

    clean.to_csv(out_dir / "raw_indicators_clean.csv", index=False)
    country_year.to_csv(out_dir / "education_analytics.csv", index=False)
    summary.to_csv(out_dir / "country_summary.csv", index=False)
    counts = {
        "raw_indicators_clean": len(clean),
        "education_analytics": len(country_year),
        "country_summary": len(summary),
    }
    logger.info("Transformation outputs written to %s: %s", out_dir, counts)
    return counts


def main():
    """Command-line entry point.

    Returns:
        int: Process exit code.
    """
    config = load_config()
    log_cfg = config.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, os.environ.get(
            "LOG_LEVEL", log_cfg.get("level", "INFO")).upper(), logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
    )
    try:
        transform(config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
