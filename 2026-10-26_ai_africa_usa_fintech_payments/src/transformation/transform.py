"""Transformation stage of the AI-Driven Fintech & Digital Payments (Africa + USA) pipeline.

Reads the World Bank CSVs produced by ``src/ingestion/ingest.py`` and turns
them into two analysis-ready tables:

``data/processed/fintech_master.csv``
    One row per country and year with every indicator side by side plus the
    derived metrics below.

``data/processed/country_summary.csv``
    One row per country holding the latest available value (and its year) for
    each indicator, growth figures and the derived metrics.

Derived metrics
---------------
fintech_readiness_score
    Weighted composite (0-100) of financial inclusion (40%), internet use (30%)
    and mobile subscriptions capped at 100 per 100 people (30%). Financial
    inclusion comes from the Global Findex survey, which only runs every few
    years, so the latest earlier survey value is carried forward within each
    country (``financial_inclusion_pct_filled``) before scoring.

remittance_to_gdp_ratio
    Personal remittances received as a percentage of total GDP, where
    total GDP = GDP per capita x population (SP.POP.TOTL). When the population
    file is missing the ratio is left empty instead of being guessed.

Usage::

    python src/transformation/transform.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("transform")

# Raw file -> column name used in the master table
INDICATOR_COLUMNS: Dict[str, str] = {
    "financial_inclusion": "financial_inclusion_pct",
    "internet_users": "internet_users_pct",
    "mobile_subscriptions": "mobile_subs_per_100",
    "remittances": "remittances_usd",
    "gdp_per_capita": "gdp_per_capita_usd",
    "population": "population",
}

# ISO-3 code -> (region, sub-region)
REGION_MAP: Dict[str, tuple] = {
    "NGA": ("Africa", "West Africa"), "GHA": ("Africa", "West Africa"),
    "SEN": ("Africa", "West Africa"), "CIV": ("Africa", "West Africa"),
    "KEN": ("Africa", "East Africa"), "ETH": ("Africa", "East Africa"),
    "TZA": ("Africa", "East Africa"), "UGA": ("Africa", "East Africa"),
    "RWA": ("Africa", "East Africa"), "CMR": ("Africa", "Central Africa"),
    "ZAF": ("Africa", "Southern Africa"), "ZMB": ("Africa", "Southern Africa"),
    "EGY": ("Africa", "North Africa"), "MAR": ("Africa", "North Africa"),
    "TUN": ("Africa", "North Africa"), "USA": ("North America", "United States"),
}

DEFAULT_WEIGHTS = {"financial_inclusion": 0.4, "internet_users": 0.3, "mobile_subscriptions": 0.3}


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ``config/config.yaml`` (or ``$CONFIG_PATH``) into a dictionary."""
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def read_indicator(raw_dir: Path, key: str, filename: str, required: bool = True) -> Optional[pd.DataFrame]:
    """Read and clean one raw indicator CSV.

    Cleaning steps: normalise the value column name (``value`` or
    ``value_usd``), strip whitespace from identifiers, cast ``year`` to int and
    the value to float, drop rows with null keys/values and de-duplicate on
    (country_code, year) keeping the last occurrence.

    Args:
        raw_dir: Directory containing the raw CSV files.
        key: Indicator key (e.g. ``"internet_users"``) used to name the column.
        filename: CSV filename inside ``raw_dir``.
        required: When False a missing file returns ``None`` instead of raising.

    Returns:
        DataFrame with ``country_code, country_name, year, <indicator column>``.
    """
    path = raw_dir / filename
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required raw file missing: {path}")
        logger.warning("Optional raw file missing, skipping: %s", path)
        return None

    df = pd.read_csv(path)
    value_col = "value_usd" if "value_usd" in df.columns else "value"
    target = INDICATOR_COLUMNS[key]
    df = df.rename(columns={value_col: target})

    before = len(df)
    df["country_code"] = df["country_code"].astype(str).str.strip().str.upper()
    df["country_name"] = df["country_name"].astype(str).str.strip()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df.dropna(subset=["country_code", "year", target])
    df = df[df[target] >= 0]
    df["year"] = df["year"].astype(int)
    df[target] = df[target].astype(float)
    df = df.drop_duplicates(subset=["country_code", "year"], keep="last")
    logger.info("%-22s %4d rows read, %4d kept after cleaning", key, before, len(df))
    return df[["country_code", "country_name", "year", target]]


def build_master(frames: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Outer-join all indicator frames on (country_code, year).

    Country names are taken from the first frame that provides them so that
    rows present in only one indicator still get a name.
    """
    names = pd.concat([f[["country_code", "country_name"]] for f in frames.values()])
    names = names.drop_duplicates("country_code").set_index("country_code")["country_name"]

    master: Optional[pd.DataFrame] = None
    for frame in frames.values():
        part = frame.drop(columns=["country_name"])
        master = part if master is None else master.merge(part, on=["country_code", "year"], how="outer")

    master["country_name"] = master["country_code"].map(names)
    master["region"] = master["country_code"].map(lambda c: REGION_MAP.get(c, ("Other", "Other"))[0])
    master["sub_region"] = master["country_code"].map(lambda c: REGION_MAP.get(c, ("Other", "Other"))[1])
    for col in INDICATOR_COLUMNS.values():
        if col not in master.columns:
            master[col] = np.nan
    return master.sort_values(["country_code", "year"]).reset_index(drop=True)


def add_derived_metrics(master: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Compute fintech_readiness_score, remittance_to_gdp_ratio and YoY growth columns.

    Args:
        master: Output of :func:`build_master`.
        config: Pipeline config (reads the ``scoring`` section if present).

    Returns:
        A copy of ``master`` with the derived columns appended.
    """
    df = master.copy()
    scoring = config.get("scoring", {}) or {}
    weights = scoring.get("fintech_readiness_weights", DEFAULT_WEIGHTS)
    mobile_cap = float(scoring.get("mobile_cap_per_100", 100))

    # Findex is a periodic survey: carry the last survey forward within a country.
    df["financial_inclusion_pct_filled"] = df.groupby("country_code")["financial_inclusion_pct"].ffill()
    mobile_capped = df["mobile_subs_per_100"].clip(upper=mobile_cap) / mobile_cap * 100

    df["fintech_readiness_score"] = (
        weights["financial_inclusion"] * df["financial_inclusion_pct_filled"]
        + weights["internet_users"] * df["internet_users_pct"]
        + weights["mobile_subscriptions"] * mobile_capped
    ).round(2)

    total_gdp = df["gdp_per_capita_usd"] * df["population"]
    df["total_gdp_usd"] = total_gdp
    df["remittance_to_gdp_ratio"] = (df["remittances_usd"] / total_gdp.replace(0, np.nan) * 100).round(3)

    df = df.sort_values(["country_code", "year"])
    df["internet_users_yoy_pp"] = df.groupby("country_code")["internet_users_pct"].diff().round(3)
    remit_growth = df.groupby("country_code")["remittances_usd"].pct_change(fill_method=None)
    df["remittances_yoy_pct"] = (remit_growth * 100).round(2)
    return df.reset_index(drop=True)


def latest_value(df: pd.DataFrame, column: str) -> pd.DataFrame:
    """Return the most recent non-null value of ``column`` and its year per country."""
    sub = df.dropna(subset=[column]).sort_values("year").groupby("country_code").last()
    return sub[[column, "year"]].rename(columns={"year": f"{column}_year"})


def build_country_summary(master: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the master table to one row per country.

    Includes the latest value/year of each indicator and derived metric, the
    change in financial inclusion between the 2017 and latest Findex rounds and
    the internet-use change between 2020 and the latest year.
    """
    base = master.groupby("country_code")[["country_name", "region", "sub_region"]].first()
    cols = list(INDICATOR_COLUMNS.values()) + ["fintech_readiness_score", "remittance_to_gdp_ratio"]
    summary = base.join([latest_value(master, c) for c in cols])

    fi = master.dropna(subset=["financial_inclusion_pct"])
    fi_2017 = fi[fi["year"] == 2017].set_index("country_code")["financial_inclusion_pct"]
    summary["financial_inclusion_2017"] = fi_2017
    has_newer_round = summary["financial_inclusion_pct_year"] > 2017
    fi_change = (summary["financial_inclusion_pct"] - fi_2017).where(has_newer_round)
    summary["financial_inclusion_change_pp"] = fi_change.round(2)

    net = master.dropna(subset=["internet_users_pct"])
    net_2020 = net[net["year"] == 2020].set_index("country_code")["internet_users_pct"]
    summary["internet_users_change_since_2020_pp"] = (summary["internet_users_pct"] - net_2020).round(2)

    summary["readiness_rank"] = summary["fintech_readiness_score"].rank(ascending=False, method="min").astype("Int64")
    return summary.reset_index().sort_values("readiness_rank").reset_index(drop=True)


def transform(config: Dict[str, Any]) -> Dict[str, int]:
    """Run the full transformation and write both processed CSV files.

    Returns:
        Mapping of output filename -> row count.
    """
    raw_dir = PROJECT_ROOT / os.environ.get("RAW_DATA_DIR", config["paths"]["raw_data_dir"])
    out_dir = PROJECT_ROOT / os.environ.get("PROCESSED_DATA_DIR", config["paths"]["processed_data_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    api = config["world_bank_api"]
    frames: Dict[str, pd.DataFrame] = {}
    for key, spec in api["indicators"].items():
        frames[key] = read_indicator(raw_dir, key, spec["file"], required=True)
    for key, spec in (api.get("supplementary_indicators") or {}).items():
        frame = read_indicator(raw_dir, key, spec["file"], required=False)
        if frame is not None:
            frames[key] = frame

    master = add_derived_metrics(build_master(frames), config)
    summary = build_country_summary(master)

    master_path = out_dir / "fintech_master.csv"
    summary_path = out_dir / "country_summary.csv"
    master.to_csv(master_path, index=False)
    summary.to_csv(summary_path, index=False)
    logger.info("Wrote %d rows -> %s", len(master), master_path)
    logger.info("Wrote %d rows -> %s", len(summary), summary_path)
    return {master_path.name: len(master), summary_path.name: len(summary)}


def main() -> int:
    """CLI entry point. Returns a process exit code."""
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO"))),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        counts = transform(config)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1
    logger.info("Transformation complete: %s", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
