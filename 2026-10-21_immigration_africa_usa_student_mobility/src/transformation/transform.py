"""Transformation stage: clean, normalise and enrich the 6 World Bank datasets.

Inputs  (data/raw/):  wb_*.json in World Bank ``[metadata, records]`` layout.
Outputs (data/processed/):
    countries.csv             - dimension: 15 African focus countries + USA
    indicators.csv            - dimension: indicator metadata
    indicators_long.csv       - fact (long): iso3, indicator_key, year, value, flags
    country_year_metrics.csv  - fact (wide) + derived student-mobility metrics
    mobility_snapshot.csv     - latest-available snapshot per African country

Usage:
    python src/transformation/transform.py
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

INDICATOR_COLUMNS = [
    "net_migration",
    "tertiary_enrollment",
    "education_expenditure",
    "remittances",
    "gdp_per_capita",
    "population",
]


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --------------------------------------------------------------------------- #
# Parsing                                                                      #
# --------------------------------------------------------------------------- #
def parse_worldbank_json(path: Path, indicator_key: str) -> pd.DataFrame:
    """Parse one World Bank JSON file into a flat DataFrame."""
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        raise ValueError(f"{path.name}: not a valid World Bank [metadata, records] payload")

    rows = []
    for rec in payload[1]:
        rows.append(
            {
                "iso3": (rec.get("countryiso3code") or "").strip().upper(),
                "country_name_wb": (rec.get("country") or {}).get("value"),
                "indicator_code": (rec.get("indicator") or {}).get("id"),
                "indicator_name": (rec.get("indicator") or {}).get("value"),
                "year": rec.get("date"),
                "value": rec.get("value"),
                "obs_status": rec.get("obs_status") or None,
            }
        )
    df = pd.DataFrame(rows)
    df["indicator_key"] = indicator_key
    logger.info("Parsed %s: %d raw records", path.name, len(df))
    return df


def clean_indicator(df: pd.DataFrame, valid_iso3: set[str], min_year: int,
                    max_year: int) -> pd.DataFrame:
    """Filter to scope, cast types, drop duplicates and impossible values."""
    out = df[df["iso3"].isin(valid_iso3)].copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna(subset=["year"])
    out = out[(out["year"] >= min_year) & (out["year"] <= max_year)]
    out["year"] = out["year"].astype(int)

    # Domain sanity rules: these indicators can never be negative.
    non_negative = {"tertiary_enrollment", "education_expenditure", "remittances",
                    "gdp_per_capita", "population"}
    key = out["indicator_key"].iloc[0] if len(out) else None
    if key in non_negative:
        bad = out["value"] < 0
        if bad.any():
            logger.warning("%s: nulling %d negative values", key, int(bad.sum()))
            out.loc[bad, "value"] = np.nan

    out = (out.sort_values(["iso3", "year"])
              .drop_duplicates(subset=["iso3", "indicator_key", "year"], keep="last"))
    return out.reset_index(drop=True)


def fill_gaps(df: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Linearly interpolate short interior gaps per country; flag imputed rows."""
    parts = []
    for iso3, grp in df.groupby("iso3", sort=False):
        grp = grp.sort_values("year").copy()
        grp["is_imputed"] = 0
        missing = grp["value"].isna()
        filled = grp["value"].interpolate(method="linear", limit=limit,
                                          limit_area="inside")
        grp.loc[missing & filled.notna(), "is_imputed"] = 1
        grp["value"] = filled
        parts.append(grp)
    return pd.concat(parts, ignore_index=True) if parts else df.assign(is_imputed=0)


# --------------------------------------------------------------------------- #
# Dimensions                                                                   #
# --------------------------------------------------------------------------- #
def build_countries(cfg: dict[str, Any], names_from_wb: dict[str, str]) -> pd.DataFrame:
    rows = []
    for iso3, meta in cfg["countries"]["africa"].items():
        rows.append({"iso3": iso3, "country_name": meta["name"],
                     "region": "Africa", "sub_region": meta["sub_region"],
                     "is_africa": 1, "is_usa": 0,
                     "wb_name": names_from_wb.get(iso3, meta["name"])})
    usa = cfg["countries"]["usa"]
    rows.append({"iso3": usa, "country_name": "United States", "region": "North America",
                 "sub_region": "North America", "is_africa": 0, "is_usa": 1,
                 "wb_name": names_from_wb.get(usa, "United States")})
    return pd.DataFrame(rows)


def build_indicators(cfg: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"indicator_key": k, "indicator_code": v["code"], "indicator_name": v["name"],
          "unit": v["unit"], "source": "World Bank WDI"}
         for k, v in cfg["worldbank"]["indicators"].items()]
    )


# --------------------------------------------------------------------------- #
# Enrichment                                                                   #
# --------------------------------------------------------------------------- #
def _minmax(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(np.where(series.notna(), 0.5, np.nan), index=series.index)
    return (series - lo) / (hi - lo)


def enrich(wide: pd.DataFrame, countries: pd.DataFrame, usa_iso3: str,
           weights: dict[str, float]) -> pd.DataFrame:
    """Add per-capita, USA-gap, growth and composite mobility-pressure metrics."""
    df = wide.merge(countries[["iso3", "country_name", "region", "sub_region", "is_africa"]],
                    on="iso3", how="left")
    pop = df["population"].replace(0, np.nan)

    df["net_migration_rate_per_1000"] = df["net_migration"] / pop * 1000
    df["remittances_per_capita_usd"] = df["remittances"] / pop
    gdp_total = df["gdp_per_capita"] * df["population"]
    df["remittances_pct_gdp"] = df["remittances"] / gdp_total.replace(0, np.nan) * 100
    df["education_spend_per_capita_usd"] = (
        df["education_expenditure"] / 100 * df["gdp_per_capita"]
    )

    usa = (df[df["iso3"] == usa_iso3]
           .set_index("year")[["gdp_per_capita", "tertiary_enrollment",
                               "education_spend_per_capita_usd"]]
           .rename(columns=lambda c: f"usa_{c}"))
    df = df.merge(usa, left_on="year", right_index=True, how="left")
    df["gdp_pc_pct_of_usa"] = df["gdp_per_capita"] / df["usa_gdp_per_capita"] * 100
    df["enrollment_gap_vs_usa_pp"] = df["usa_tertiary_enrollment"] - df["tertiary_enrollment"]
    df["edu_spend_pc_pct_of_usa"] = (
        df["education_spend_per_capita_usd"] / df["usa_education_spend_per_capita_usd"] * 100
    )
    df = df.drop(columns=[c for c in df.columns if c.startswith("usa_")])

    df = df.sort_values(["iso3", "year"]).reset_index(drop=True)
    for col in ["gdp_per_capita", "remittances", "tertiary_enrollment", "population"]:
        df[f"{col}_yoy_pct"] = df.groupby("iso3")[col].pct_change(fill_method=None) * 100

    # Student Mobility Pressure Index (0-100): push factors for outbound study,
    # normalised within each year across the African focus countries only.
    # Tertiary enrollment is reported with a long lag (and the USA series ends
    # earlier than many African ones), so the education component uses each
    # country's own last observed enrollment carried forward. Within a year,
    # min-max scaling of (USA - x) equals 1 - scaling of x, so no USA value is
    # needed. ``enrollment_reference_year`` records which observation was used.
    df["enrollment_reference_year"] = (
        df["year"].where(df["tertiary_enrollment"].notna())
        .groupby(df["iso3"]).ffill()
    )
    df["enrollment_for_index"] = df.groupby("iso3")["tertiary_enrollment"].ffill()
    df["mobility_pressure_index"] = np.nan
    africa_mask = df["is_africa"] == 1
    for year, grp in df[africa_mask].groupby("year"):
        comp = pd.DataFrame(index=grp.index)
        comp["enrollment_gap"] = 1 - _minmax(grp["enrollment_for_index"])
        comp["income_gap"] = 1 - _minmax(np.log(grp["gdp_per_capita"]))
        comp["remittance_intensity"] = _minmax(grp["remittances_pct_gdp"])
        comp["emigration_rate"] = 1 - _minmax(grp["net_migration_rate_per_1000"])
        w = pd.Series(weights)
        avail = comp.notna()
        weighted = comp.fillna(0).mul(w, axis=1).sum(axis=1)
        weight_sum = avail.mul(w, axis=1).sum(axis=1)
        # Require both core components (education gap and income gap); missing
        # secondary components are handled by re-weighting.
        core_ok = avail["enrollment_gap"] & avail["income_gap"]
        score = np.where(core_ok, weighted / weight_sum.replace(0, np.nan) * 100, np.nan)
        df.loc[grp.index, "mobility_pressure_index"] = score

    df["income_group_proxy"] = pd.cut(
        df["gdp_per_capita"], bins=[0, 1145, 4515, 14005, np.inf],
        labels=["Low", "Lower-middle", "Upper-middle", "High"],
    ).astype(str).replace("nan", None)

    df = df.drop(columns=["enrollment_for_index"])
    round_cols = [c for c in df.columns if df[c].dtype.kind == "f"]
    df[round_cols] = df[round_cols].round(4)
    return df.reset_index(drop=True)


def build_snapshot(metrics: pd.DataFrame) -> pd.DataFrame:
    """Latest non-null value of each key metric per African country."""
    cols = ["tertiary_enrollment", "education_expenditure", "gdp_per_capita",
            "remittances", "remittances_pct_gdp", "net_migration_rate_per_1000",
            "population", "gdp_pc_pct_of_usa", "enrollment_gap_vs_usa_pp",
            "mobility_pressure_index"]
    africa = metrics[metrics["is_africa"] == 1].sort_values("year")
    rows = []
    for iso3, grp in africa.groupby("iso3"):
        row = {"iso3": iso3, "country_name": grp["country_name"].iloc[0],
               "sub_region": grp["sub_region"].iloc[0]}
        for col in cols:
            valid = grp.dropna(subset=[col])
            row[col] = valid[col].iloc[-1] if len(valid) else np.nan
            row[f"{col}_year"] = int(valid["year"].iloc[-1]) if len(valid) else None
        rows.append(row)
    snap = pd.DataFrame(rows)
    snap["pressure_rank"] = snap["mobility_pressure_index"].rank(ascending=False,
                                                                 method="min").astype("Int64")
    return snap.sort_values("pressure_rank").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #
def transform(config_path: str | None = None) -> dict[str, int]:
    cfg = load_config(config_path)
    raw_dir = PROJECT_ROOT / cfg["paths"]["raw_dir"]
    out_dir = PROJECT_ROOT / cfg["paths"]["processed_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    tcfg = cfg["transform"]
    usa_iso3 = cfg["countries"]["usa"]
    valid_iso3 = set(cfg["countries"]["africa"]) | {usa_iso3}

    frames, wb_names = [], {}
    for key, spec in cfg["worldbank"]["indicators"].items():
        path = raw_dir / spec["file"]
        if not path.exists():
            raise FileNotFoundError(f"Missing raw file {path}; run ingestion first")
        parsed = parse_worldbank_json(path, key)
        wb_names.update(parsed.dropna(subset=["country_name_wb"])
                        .set_index("iso3")["country_name_wb"].to_dict())
        cleaned = clean_indicator(parsed, valid_iso3, tcfg["min_year"], tcfg["max_year"])
        if cleaned.empty:
            logger.warning("%s: no in-scope records after cleaning", key)
            continue
        filled = fill_gaps(cleaned, int(tcfg["interpolate_limit"]))
        logger.info("%s: %d in-scope rows, %d non-null, %d imputed", key, len(filled),
                    int(filled["value"].notna().sum()), int(filled["is_imputed"].sum()))
        frames.append(filled)

    if not frames:
        raise RuntimeError("No in-scope data found in data/raw/")

    long_df = pd.concat(frames, ignore_index=True)
    long_df = long_df[["iso3", "indicator_key", "indicator_code", "year", "value",
                       "is_imputed", "obs_status"]]
    long_df = long_df.dropna(subset=["value"]).sort_values(
        ["indicator_key", "iso3", "year"]).reset_index(drop=True)

    countries = build_countries(cfg, wb_names)
    indicators = build_indicators(cfg)

    wide = (long_df.pivot_table(index=["iso3", "year"], columns="indicator_key",
                                values="value", aggfunc="last")
                   .reset_index())
    wide.columns.name = None
    for col in INDICATOR_COLUMNS:
        if col not in wide.columns:
            wide[col] = np.nan

    metrics = enrich(wide, countries, usa_iso3, tcfg["mobility_pressure_weights"])
    snapshot = build_snapshot(metrics)

    outputs = {
        "countries.csv": countries,
        "indicators.csv": indicators,
        "indicators_long.csv": long_df,
        "country_year_metrics.csv": metrics,
        "mobility_snapshot.csv": snapshot,
    }
    counts = {}
    for name, frame in outputs.items():
        frame.to_csv(out_dir / name, index=False)
        counts[name] = len(frame)
        logger.info("Wrote %s (%d rows)", (out_dir / name).relative_to(PROJECT_ROOT), len(frame))
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Transform raw World Bank JSON to CSVs")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    try:
        transform(args.config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
