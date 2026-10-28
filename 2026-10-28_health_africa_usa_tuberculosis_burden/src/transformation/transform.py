"""
Transformation module for the Africa & USA Tuberculosis Burden pipeline.

Reads the CSV extracts and raw World Bank JSON written by the ingestion step,
then:

1. Cleans each indicator - trims strings, casts types, drops impossible values
   (negative rates, success rates outside 0-100), removes rows with missing
   keys and de-duplicates on ``(country_code, year)``.
2. Normalises geography - attaches ``region`` (Africa / USA) and an African
   ``sub_region`` from ``config.yaml``.
3. Enriches - incidence burden band, uncertainty width, year-over-year change,
   case-fatality proxy (mortality / incidence), gap to the 90 % WHO treatment
   success target, and a health-expenditure join.
4. Merges incidence, mortality and treatment success into one country-year
   table (outer join so no observation is lost).

Outputs are written to ``data/processed/``.

Usage::

    python src/transformation/transform.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
KEYS = ["country_code", "year"]

logger = logging.getLogger("tb_transform")


# ---------------------------------------------------------------------------
# Config / IO helpers
# ---------------------------------------------------------------------------
def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load YAML configuration (``CONFIG_PATH`` env var overrides default)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve raw and processed directories, creating the latter."""
    raw = PROJECT_ROOT / config["paths"]["raw_dir"]
    processed = PROJECT_ROOT / config["paths"]["processed_dir"]
    processed.mkdir(parents=True, exist_ok=True)
    return {"raw": raw, "processed": processed}


def geography_lookup(config: Dict[str, Any]) -> pd.DataFrame:
    """Return a DataFrame mapping country_code -> region / sub_region."""
    rows = [
        {"country_code": code, "region": "Africa", "sub_region": sub.title()}
        for sub, codes in config["geography"]["africa_codes"].items()
        for code in codes
    ]
    rows.append({"country_code": config["geography"]["usa_code"],
                 "region": "USA", "sub_region": "North America"})
    return pd.DataFrame(rows)


def read_indicator(raw_dir: Path, files: List[str]) -> pd.DataFrame:
    """Concatenate one indicator's Africa and USA CSV extracts."""
    frames = []
    for name in files:
        path = raw_dir / name
        if not path.exists():
            logger.warning("Missing raw extract %s - skipping", path.name)
            continue
        frames.append(pd.read_csv(path))
        logger.info("Read %s (%d rows)", path.name, len(frames[-1]))
    if not frames:
        raise FileNotFoundError(f"None of {files} found in {raw_dir}; run ingestion first")
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
def clean_indicator(df: pd.DataFrame, value_cols: List[str], min_year: int,
                    upper_bound: float | None = None) -> pd.DataFrame:
    """Generic cleaning for a tidy ``country_code, country_name, year, value...`` frame.

    * strips whitespace and upper-cases ISO codes
    * casts ``year`` to int and value columns to float (coercing junk to NaN)
    * drops rows with missing keys or a missing primary value
    * drops negative values and values above ``upper_bound``
    * filters to ``year >= min_year``
    * de-duplicates on (country_code, year) keeping the last record
    """
    before = len(df)
    out = df.copy()
    out["country_code"] = out["country_code"].astype(str).str.strip().str.upper()
    out["country_name"] = out["country_name"].astype(str).str.strip()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    for col in value_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)

    primary = value_cols[0]
    out = out.dropna(subset=["country_code", "year", primary])
    out = out[out[primary] >= 0]
    if upper_bound is not None:
        out = out[out[primary] <= upper_bound]
    out["year"] = out["year"].astype(int)
    out = out[out["year"] >= min_year]
    out = (out.sort_values(KEYS)
              .drop_duplicates(subset=KEYS, keep="last")
              .reset_index(drop=True))
    logger.info("Cleaned %s: %d -> %d rows", primary, before, len(out))
    return out


def clean_incidence(df: pd.DataFrame, min_year: int, bands: Dict[str, float]) -> pd.DataFrame:
    """Clean incidence and add uncertainty / burden-band / YoY features."""
    out = clean_indicator(df, ["tb_incidence_per_100k", "low_estimate", "high_estimate"], min_year)
    # Fill missing bounds with the point estimate, and enforce low <= point <= high.
    out["low_estimate"] = out["low_estimate"].fillna(out["tb_incidence_per_100k"])
    out["high_estimate"] = out["high_estimate"].fillna(out["tb_incidence_per_100k"])
    out["low_estimate"] = out[["low_estimate", "tb_incidence_per_100k"]].min(axis=1)
    out["high_estimate"] = out[["high_estimate", "tb_incidence_per_100k"]].max(axis=1)
    out["uncertainty_range"] = out["high_estimate"] - out["low_estimate"]
    out["burden_category"] = pd.cut(
        out["tb_incidence_per_100k"],
        bins=[-np.inf, bands["low"], bands["moderate"], bands["high"], np.inf],
        labels=["Low", "Moderate", "High", "Very High"],
        right=False,
    ).astype(str)
    out["incidence_yoy_change_pct"] = (
        out.groupby("country_code")["tb_incidence_per_100k"].pct_change(fill_method=None) * 100
    ).round(2)
    return out


def parse_world_bank_json(path: Path, value_name: str, codes: List[str],
                          min_year: int) -> pd.DataFrame:
    """Parse a saved World Bank ``[meta, records]`` document into a tidy frame."""
    if not path.exists():
        logger.warning("%s not found - returning empty frame", path.name)
        return pd.DataFrame(columns=["country_code", "country_name", "year", value_name])
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    records = payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []
    df = pd.DataFrame(
        {
            "country_code": [r.get("countryiso3code") for r in records],
            "country_name": [r.get("country", {}).get("value") for r in records],
            "year": [r.get("date") for r in records],
            value_name: [r.get("value") for r in records],
        }
    )
    df = df[df["country_code"].isin(codes)]
    return clean_indicator(df, [value_name], min_year)


# ---------------------------------------------------------------------------
# Merge & enrichment
# ---------------------------------------------------------------------------
def build_countries(frames: List[pd.DataFrame], geo: pd.DataFrame) -> pd.DataFrame:
    """Build the country dimension (one row per code) with region metadata."""
    names = (pd.concat([f[["country_code", "country_name"]] for f in frames])
               .drop_duplicates("country_code", keep="first"))
    countries = geo.merge(names, on="country_code", how="left")
    countries["country_name"] = countries["country_name"].fillna(countries["country_code"])
    return countries[["country_code", "country_name", "region", "sub_region"]] \
        .sort_values(["region", "country_name"]).reset_index(drop=True)


def merge_tb(incidence: pd.DataFrame, mortality: pd.DataFrame, success: pd.DataFrame,
             health: pd.DataFrame, countries: pd.DataFrame, target_pct: float) -> pd.DataFrame:
    """Outer-join the TB indicators on (country_code, year) and enrich."""
    merged = (
        incidence.drop(columns="country_name")
        .merge(mortality.drop(columns="country_name"), on=KEYS, how="outer")
        .merge(success.drop(columns="country_name"), on=KEYS, how="outer")
        .merge(health.drop(columns="country_name"), on=KEYS, how="left")
        .merge(countries, on="country_code", how="inner")
    )
    merged["case_fatality_ratio_pct"] = np.where(
        merged["tb_incidence_per_100k"] > 0,
        merged["tb_mortality_per_100k"] / merged["tb_incidence_per_100k"] * 100,
        np.nan,
    ).round(2)
    merged["treatment_gap_to_target_pct"] = (
        target_pct - merged["tb_treatment_success_pct"]).clip(lower=0)
    merged["meets_treatment_target"] = np.where(
        merged["tb_treatment_success_pct"].isna(), np.nan,
        (merged["tb_treatment_success_pct"] >= target_pct).astype(float))
    merged["data_completeness"] = merged[
        ["tb_incidence_per_100k", "tb_mortality_per_100k", "tb_treatment_success_pct"]
    ].notna().sum(axis=1)

    ordered = [
        "country_code", "country_name", "region", "sub_region", "year",
        "tb_incidence_per_100k", "low_estimate", "high_estimate", "uncertainty_range",
        "burden_category", "incidence_yoy_change_pct", "tb_mortality_per_100k",
        "case_fatality_ratio_pct", "tb_treatment_success_pct",
        "treatment_gap_to_target_pct", "meets_treatment_target",
        "health_expenditure_pct_gdp", "data_completeness",
    ]
    merged["burden_category"] = merged["burden_category"].replace("nan", np.nan)
    return merged[ordered].sort_values(["region", "country_code", "year"]).reset_index(drop=True)


def write(df: pd.DataFrame, path: Path) -> None:
    """Persist a DataFrame to CSV and log the result."""
    df.to_csv(path, index=False)
    logger.info("Wrote %s (%d rows, %d cols)", path.name, len(df), df.shape[1])


def transform(config: Dict[str, Any]) -> Dict[str, int]:
    """Run the full transformation; returns row counts per output file."""
    paths = get_paths(config)
    pipe = config["pipeline"]
    raw_csv = config["paths"]["raw_csv"]
    out_names = config["paths"]["processed_csv"]
    min_year = int(pipe.get("min_year", 2000))
    geo = geography_lookup(config)
    codes = geo["country_code"].tolist()

    incidence = clean_incidence(
        read_indicator(paths["raw"], [raw_csv["incidence"], raw_csv["usa_incidence"]]),
        min_year, pipe["incidence_bands"])
    mortality = clean_indicator(
        read_indicator(paths["raw"], [raw_csv["mortality"], raw_csv["usa_mortality"]]),
        ["tb_mortality_per_100k"], min_year)
    success = clean_indicator(
        read_indicator(paths["raw"], [raw_csv["treatment_success"],
                                      raw_csv["usa_treatment_success"]]),
        ["tb_treatment_success_pct"], min_year, upper_bound=100)
    health = parse_world_bank_json(
        paths["raw"] / config["data_sources"]["wb_health_expenditure"]["raw_file"],
        "health_expenditure_pct_gdp", codes, min_year)

    # Restrict every indicator to the configured geography.
    incidence, mortality, success = (
        f[f["country_code"].isin(codes)] for f in (incidence, mortality, success))

    countries = build_countries([incidence, mortality, success, health], geo)
    merged = merge_tb(incidence, mortality, success, health, countries,
                      float(pipe.get("treatment_success_target_pct", 90)))

    outputs = {
        "countries": countries,
        "incidence": incidence,
        "mortality": mortality,
        "treatment_success": success,
        "health_expenditure": health,
        "merged": merged,
    }
    counts = {}
    for key, frame in outputs.items():
        write(frame, paths["processed"] / out_names[key])
        counts[key] = len(frame)
    logger.info("Transformation complete: %s", counts)
    return counts


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Transform raw TB burden data")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        transform(config)
        return 0
    except (FileNotFoundError, KeyError, ValueError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
