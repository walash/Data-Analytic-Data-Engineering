"""Transformation stage: turn raw World Bank JSON into analysis-ready CSVs.

Reads every indicator file listed in ``config/config.yaml`` from ``data/raw/``
and writes four tidy datasets to ``data/processed/``:

* ``countries.csv``             - country dimension (ISO2, ISO3, name, sub-region)
* ``indicators_long.csv``       - one row per country / indicator / year
* ``country_year_metrics.csv``  - wide panel (one row per country-year) with derived
                                  metrics: net migration per 1,000 people, remittances
                                  per capita, YoY GDP growth
* ``brain_drain_index.csv``     - latest-value snapshot per country plus a weighted
                                  Brain Drain Pressure Index (0-100)

Usage:
    python src/transformation/transform.py [--config path] [--raw-dir dir] [--out-dir dir]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("transform")

WIDE_COLUMNS = [
    "net_migration", "remittances_usd", "remittances_pct_gdp", "gdp_per_capita",
    "unemployment", "tertiary_enrollment", "migrant_stock", "population",
]


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _resolve(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path


def parse_worldbank_file(path: Path, indicator_name: str) -> pd.DataFrame:
    """Parse one World Bank API response file into a tidy DataFrame."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        raise ValueError(f"{path} is not a valid World Bank [metadata, records] payload")

    rows = [
        {
            "country_code": rec["country"]["id"],
            "country_name": rec["country"]["value"],
            "iso3": rec.get("countryiso3code") or None,
            "indicator": indicator_name,
            "indicator_code": rec["indicator"]["id"],
            "indicator_label": rec["indicator"]["value"],
            "year": rec.get("date"),
            "value": rec.get("value"),
        }
        for rec in payload[1]
    ]
    df = pd.DataFrame(rows)
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    logger.info("Parsed %-22s %4d rows (%d null values) from %s",
                indicator_name, len(df), int(df["value"].isna().sum()), path.name)
    return df


def build_long(config: dict[str, Any], raw_dir: Path) -> pd.DataFrame:
    """Combine every configured indicator into a cleaned long table."""
    frames = []
    for name, spec in config["worldbank"]["indicators"].items():
        path = raw_dir / spec["file"]
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file for '{name}': {path}. "
                                    "Run src/ingestion/ingest.py first.")
        frames.append(parse_worldbank_file(path, name))
    long_df = pd.concat(frames, ignore_index=True)

    allowed = set(config["worldbank"]["countries"])
    before = len(long_df)
    long_df = long_df[long_df["country_code"].isin(allowed)]
    long_df = long_df.dropna(subset=["year", "value"])
    long_df = long_df.drop_duplicates(subset=["country_code", "indicator", "year"], keep="last")
    long_df = long_df.sort_values(["country_code", "indicator", "year"]).reset_index(drop=True)
    logger.info("Long table: %d rows kept of %d (dropped nulls/duplicates/out-of-scope)",
                len(long_df), before)
    return long_df


def build_countries(long_df: pd.DataFrame, regions: dict[str, str]) -> pd.DataFrame:
    countries = (long_df.sort_values("year")
                 .groupby("country_code", as_index=False)
                 .agg(country_name=("country_name", "last"), iso3=("iso3", "last")))
    countries["region"] = countries["country_code"].map(regions).fillna("Other Africa")
    return countries.sort_values("country_code").reset_index(drop=True)


def build_wide(long_df: pd.DataFrame, countries: pd.DataFrame) -> pd.DataFrame:
    """Pivot to a country-year panel and add derived brain-drain metrics."""
    wide = (long_df.pivot_table(index=["country_code", "year"], columns="indicator",
                                values="value", aggfunc="last")
            .reset_index())
    wide.columns.name = None
    for col in WIDE_COLUMNS:
        if col not in wide.columns:
            wide[col] = np.nan
    wide = wide.merge(countries[["country_code", "country_name", "region"]],
                      on="country_code", how="left")

    wide = wide.sort_values(["country_code", "year"]).reset_index(drop=True)
    # Population is only published for the latest 5 years in our pull; carry the nearest
    # observed value backwards/forwards within each country to compute per-capita rates.
    wide["population_filled"] = (wide.groupby("country_code")["population"]
                                 .transform(lambda s: s.bfill().ffill()))
    wide["net_migration_per_1000"] = wide["net_migration"] / wide["population_filled"] * 1000
    wide["remittances_per_capita_usd"] = wide["remittances_usd"] / wide["population_filled"]
    wide["migrant_stock_pct_pop"] = wide["migrant_stock"] / wide["population_filled"] * 100
    wide["gdp_per_capita_growth_pct"] = (wide.groupby("country_code")["gdp_per_capita"]
                                         .pct_change(fill_method=None) * 100)
    wide["remittances_usd_bn"] = wide["remittances_usd"] / 1e9
    wide["is_net_emigration"] = (wide["net_migration"] < 0).astype("Int64")
    wide.loc[wide["net_migration"].isna(), "is_net_emigration"] = pd.NA

    ordered = (["country_code", "country_name", "region", "year"] + WIDE_COLUMNS +
               ["population_filled", "net_migration_per_1000", "remittances_per_capita_usd",
                "remittances_usd_bn", "migrant_stock_pct_pop", "gdp_per_capita_growth_pct",
                "is_net_emigration"])
    wide = wide[ordered]
    float_cols = wide.select_dtypes(include="float").columns
    wide[float_cols] = wide[float_cols].round(4)
    logger.info("Wide panel: %d country-year rows x %d columns", *wide.shape)
    return wide


def _minmax(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if pd.isna(lo) or hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


def _latest(wide: pd.DataFrame, column: str) -> pd.DataFrame:
    sub = wide.dropna(subset=[column]).sort_values("year")
    latest = sub.groupby("country_code").tail(1)[["country_code", "year", column]]
    return latest.rename(columns={"year": f"{column}_year"})


def build_index(wide: pd.DataFrame, countries: pd.DataFrame,
                weights: dict[str, float]) -> pd.DataFrame:
    """Latest-value snapshot per country and a weighted Brain Drain Pressure Index."""
    snap = countries[["country_code", "country_name", "region"]].copy()
    for col in ["net_migration", "net_migration_per_1000", "remittances_usd",
                "remittances_pct_gdp", "gdp_per_capita", "unemployment",
                "tertiary_enrollment", "migrant_stock", "population"]:
        snap = snap.merge(_latest(wide, col), on="country_code", how="left")

    # 10-year cumulative net migration (sum over all available years in the panel).
    cum = (wide.groupby("country_code")["net_migration"].sum(min_count=1)
           .rename("net_migration_cumulative").reset_index())
    snap = snap.merge(cum, on="country_code", how="left")

    components = pd.DataFrame({
        "unemployment": _minmax(snap["unemployment"]),
        "gdp_per_capita_gap": 1 - _minmax(snap["gdp_per_capita"]),
        "net_outflow_rate": 1 - _minmax(snap["net_migration_per_1000"]),
        "remittance_dependence": _minmax(snap["remittances_pct_gdp"]),
    }).fillna(0.5)  # neutral score when a country lacks a component
    for name in components.columns:
        snap[f"score_{name}"] = components[name].round(4)

    total_weight = sum(weights.values())
    snap["brain_drain_index"] = (sum(components[k] * w for k, w in weights.items())
                                 / total_weight * 100).round(2)
    snap["brain_drain_rank"] = snap["brain_drain_index"].rank(ascending=False,
                                                              method="min").astype(int)
    snap = snap.sort_values("brain_drain_rank").reset_index(drop=True)
    logger.info("Brain Drain Pressure Index computed for %d countries (top: %s)",
                len(snap), snap.iloc[0]["country_name"] if len(snap) else "n/a")
    return snap


def transform(config_path: str | Path | None = None, raw_dir: str | Path | None = None,
              out_dir: str | Path | None = None) -> dict[str, Path]:
    config = load_config(config_path)
    raw = _resolve(raw_dir or config["paths"]["raw_dir"])
    out = _resolve(out_dir or config["paths"]["processed_dir"])
    out.mkdir(parents=True, exist_ok=True)

    long_df = build_long(config, raw)
    if long_df.empty:
        raise RuntimeError("No usable records found in raw data")
    countries = build_countries(long_df, config.get("regions", {}))
    wide = build_wide(long_df, countries)
    index = build_index(wide, countries, config["brain_drain_index"]["weights"])

    outputs = {
        "countries": (countries, out / "countries.csv"),
        "indicators_long": (long_df, out / "indicators_long.csv"),
        "country_year_metrics": (wide, out / "country_year_metrics.csv"),
        "brain_drain_index": (index, out / "brain_drain_index.csv"),
    }
    written = {}
    for name, (df, path) in outputs.items():
        df.to_csv(path, index=False)
        written[name] = path
        logger.info("Wrote %-22s %5d rows -> %s", name, len(df), path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transform raw brain-drain indicators")
    parser.add_argument("--config", default=None)
    parser.add_argument("--raw-dir", default=None)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    try:
        transform(args.config, args.raw_dir, args.out_dir)
    except Exception as exc:  # noqa: BLE001
        logger.error("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
