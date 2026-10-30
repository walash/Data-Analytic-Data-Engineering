"""Transformation stage: clean raw indicator CSVs into analysis-ready tables.

Inputs  (data/raw/):   wb_ngas_electricity_pct.csv, wb_ngas_rents_gdp_pct.csv,
                       wb_fuel_exports_pct.csv
Outputs (data/processed/):
    countries.csv              country dimension (iso3, name, region, is_focus)
    gas_indicators_long.csv    one row per country / year / indicator
    country_year_panel.csv     wide panel, one row per country / year
    pipeline_infrastructure.csv reference pipeline catalogue from config.yaml

Cleaning rules:
    * strip whitespace, upper-case ISO codes
    * cast ``year`` to int and values to float (invalid -> NaN)
    * drop rows with missing keys or values, or years / percentages out of range
    * deduplicate on (iso3, year), keeping the last occurrence
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("transform")

KEY_COLUMNS = ["country_code", "country_name", "iso3", "year"]


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def region_for(iso3: str, config: Dict[str, Any]) -> str:
    groups = config.get("countries", {})
    if iso3 in (groups.get("north_america") or []):
        return "North America"
    if iso3 in (groups.get("africa") or []):
        return "Africa"
    return "Other"


def clean_indicator(df: pd.DataFrame, value_column: str, pipeline_cfg: Dict[str, Any]) -> pd.DataFrame:
    """Apply null handling, type casting, range checks and deduplication."""
    missing = [c for c in KEY_COLUMNS + [value_column] if c not in df.columns]
    if missing:
        raise ValueError(f"{value_column}: missing columns {missing}")

    out = df[KEY_COLUMNS + [value_column]].copy()
    start_rows = len(out)

    for col in ("country_code", "country_name", "iso3"):
        out[col] = out[col].astype("string").str.strip()
    out["country_code"] = out["country_code"].str.upper()
    out["iso3"] = out["iso3"].str.upper()
    out.loc[out["iso3"] == "", "iso3"] = pd.NA

    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out[value_column] = pd.to_numeric(out[value_column], errors="coerce").replace([np.inf, -np.inf], np.nan)

    out = out.dropna(subset=["iso3", "year", value_column])
    out["country_name"] = out["country_name"].fillna(out["iso3"])
    out["year"] = out["year"].astype(int)

    min_year = int(pipeline_cfg.get("min_year", 2000))
    max_year = int(pipeline_cfg.get("max_year", 2100))
    pct_min = float(pipeline_cfg.get("pct_min", 0.0))
    pct_max = float(pipeline_cfg.get("pct_max", 100.0))
    in_range = out["year"].between(min_year, max_year) & out[value_column].between(pct_min, pct_max)
    out = out[in_range]

    before_dedup = len(out)
    out = out.drop_duplicates(subset=["iso3", "year"], keep="last")
    out[value_column] = out[value_column].astype(float).round(4)
    out = out.sort_values(["iso3", "year"]).reset_index(drop=True)

    logger.info(
        "%s: %d raw rows -> %d clean rows (%d duplicates removed)",
        value_column, start_rows, len(out), before_dedup - len(out),
    )
    return out


def read_raw(raw_dir: Path, filename: str) -> pd.DataFrame:
    path = raw_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"Raw file not found: {path}. Run ingest.py first.")
    return pd.read_csv(path, dtype=str, keep_default_na=True, na_values=["", "NA", "null", ".."])


def build_countries(frames: List[pd.DataFrame], config: Dict[str, Any]) -> pd.DataFrame:
    focus = set(config.get("countries", {}).get("focus") or [])
    names = pd.concat([f[["iso3", "country_code", "country_name"]] for f in frames], ignore_index=True)
    names = names.drop_duplicates(subset=["iso3"], keep="last").copy()
    names["region"] = names["iso3"].map(lambda c: region_for(c, config))
    names["is_focus"] = names["iso3"].isin(focus).astype(int)
    return names.sort_values(["region", "country_name"]).reset_index(drop=True)


def build_long(cleaned: Dict[str, pd.DataFrame], config: Dict[str, Any]) -> pd.DataFrame:
    indicators = config["worldbank"]["indicators"]
    parts = []
    for key, df in cleaned.items():
        part = df.rename(columns={key: "value"})[["iso3", "year", "value"]].copy()
        part["indicator_key"] = key
        part["indicator_code"] = indicators[key]["code"]
        parts.append(part)
    long_df = pd.concat(parts, ignore_index=True)
    return long_df[["iso3", "year", "indicator_key", "indicator_code", "value"]].sort_values(
        ["indicator_key", "iso3", "year"]
    ).reset_index(drop=True)


def build_panel(cleaned: Dict[str, pd.DataFrame], countries: pd.DataFrame) -> pd.DataFrame:
    panel: Optional[pd.DataFrame] = None
    for key, df in cleaned.items():
        sub = df[["iso3", "year", key]]
        panel = sub if panel is None else panel.merge(sub, on=["iso3", "year"], how="outer")
    panel = panel.merge(countries[["iso3", "country_name", "region"]], on="iso3", how="left")

    # Year-over-year change per indicator, only where the prior row is year - 1.
    panel = panel.sort_values(["iso3", "year"]).reset_index(drop=True)
    consecutive = panel.groupby("iso3")["year"].diff().eq(1)
    for key in cleaned:
        delta = panel.groupby("iso3")[key].diff().round(4)
        panel[f"{key}_yoy_change"] = delta.where(consecutive)
    ordered = ["iso3", "country_name", "region", "year"] + [
        c for c in panel.columns if c not in {"iso3", "country_name", "region", "year"}
    ]
    return panel[ordered]


def build_pipelines(config: Dict[str, Any]) -> pd.DataFrame:
    records = config.get("reference_pipelines") or []
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["name", "origin_iso3", "destination", "region",
                                     "length_km", "capacity_bcm_y", "start_year", "status"])
    df["name"] = df["name"].astype(str).str.strip()
    df["origin_iso3"] = df["origin_iso3"].astype(str).str.upper().str.strip()
    for col in ("length_km", "capacity_bcm_y"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["start_year"] = pd.to_numeric(df["start_year"], errors="coerce").astype("Int64")
    df["status"] = df["status"].fillna("Unknown")
    df = df.drop_duplicates(subset=["name"], keep="last")
    return df.sort_values("capacity_bcm_y", ascending=False).reset_index(drop=True)


def transform(config: Dict[str, Any]) -> Dict[str, int]:
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    processed_dir = PROJECT_ROOT / config["paths"]["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    pipeline_cfg = config.get("pipeline", {})

    cleaned: Dict[str, pd.DataFrame] = {}
    for key, spec in config["worldbank"]["indicators"].items():
        cleaned[key] = clean_indicator(read_raw(raw_dir, spec["raw_file"]), key, pipeline_cfg)

    countries = build_countries(list(cleaned.values()), config)
    outputs = {
        "countries": countries,
        "gas_indicators_long": build_long(cleaned, config),
        "country_year_panel": build_panel(cleaned, countries),
        "pipeline_infrastructure": build_pipelines(config),
    }
    counts: Dict[str, int] = {}
    for name, df in outputs.items():
        path = processed_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        counts[name] = len(df)
        logger.info("Wrote %d rows -> %s", len(df), path)
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Transform raw natural gas indicators")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Transformation summary: %s", transform(config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
