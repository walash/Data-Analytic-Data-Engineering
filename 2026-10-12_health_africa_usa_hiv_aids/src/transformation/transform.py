"""Transformation stage for the HIV/AIDS Africa & USA pipeline.

Reads the five raw indicator CSVs from ``data/raw/``, cleans them (type
casting, null handling, de-duplication), merges them into a single wide
country-year table and derives analytical metrics:

* ``plhiv_on_art``        - estimated people receiving ART (PLHIV x ART%)
* ``treatment_gap``       - estimated PLHIV *not* on ART (PLHIV - on ART)
* ``treatment_gap_pct``   - share of PLHIV not on ART (100 - ART%)
* ``meets_90_90_90_art``  - ART coverage >= 81% (90% diagnosed x 90% treated)
* ``meets_95_95_95_art``  - ART coverage >= 90.25%
* ``*_yoy_change``        - year-over-year change per country
* ``region`` / ``subregion`` - Africa vs USA grouping

Outputs:

* ``data/processed/hiv_aids_processed.csv`` - one row per country-year
* ``data/processed/hiv_aids_summary.csv``   - one row per country (latest values
  and 2015->latest change)

Note: the World Bank publishes no HIV prevalence/ART/incidence/PLHIV values for
the United States (UNAIDS does not report modelled estimates for it), so the US
rows only carry health expenditure. Nulls are preserved, never imputed across
countries.
"""

from __future__ import annotations

import argparse
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
KEYS = ["country_code", "year"]

logger = logging.getLogger("hiv_aids.transform")


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML configuration."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def country_dimension(config: dict[str, Any]) -> pd.DataFrame:
    """Build the country lookup (code, iso3, region, subregion) from config."""
    rows = []
    for region_key, region_label in (("africa", "Africa"), ("usa", "USA")):
        for c in config["countries"][region_key]:
            rows.append({"country_code": c["code"], "iso3": c["iso3"],
                         "region": region_label, "subregion": c["subregion"]})
    return pd.DataFrame(rows)


def read_indicator(raw_dir: Path, key: str, value_column: str) -> pd.DataFrame:
    """Read and clean one raw indicator CSV."""
    path = raw_dir / f"{key}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing raw file: {path}")
    df = pd.read_csv(path, dtype={"country_code": "string", "iso3": "string"},
                     keep_default_na=False, na_values=[""])
    required = {"country_code", "country_name", "year", value_column}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")

    df["country_code"] = df["country_code"].str.strip().str.upper()
    df["country_name"] = df["country_name"].astype(str).str.strip()
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce")
    df = df.dropna(subset=["country_code", "year"])

    before = len(df)
    # Prefer non-null observations when duplicates exist for a country-year.
    df = (df.sort_values(value_column, na_position="first")
            .drop_duplicates(subset=KEYS, keep="last"))
    if before != len(df):
        logger.warning("%s: removed %d duplicate rows", key, before - len(df))
    logger.info("%s: %d rows, %d non-null values", key, len(df), df[value_column].notna().sum())
    return df[["country_code", "country_name", "year", value_column]]


def merge_indicators(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Outer-join all indicator frames on country_code + year."""
    names = pd.concat([f[["country_code", "country_name"]] for f in frames]).drop_duplicates(
        "country_code")
    merged = frames[0].drop(columns="country_name")
    for frame in frames[1:]:
        merged = merged.merge(frame.drop(columns="country_name"), on=KEYS, how="outer")
    return merged.merge(names, on="country_code", how="left")


def add_derived_metrics(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Compute treatment gap, target flags and year-over-year changes."""
    targets = config["pipeline"]["targets"]
    df = df.sort_values(KEYS).reset_index(drop=True)

    # Clip percentages to their valid domain to guard against bad source values.
    for col in ("hiv_prevalence_pct", "art_coverage_pct", "health_exp_pct_gdp"):
        df[col] = df[col].clip(lower=0, upper=100)

    df["plhiv_on_art"] = (df["plhiv_count"] * df["art_coverage_pct"] / 100).round()
    df["treatment_gap"] = (df["plhiv_count"] - df["plhiv_on_art"]).round()
    df["treatment_gap_pct"] = (100 - df["art_coverage_pct"]).round(2)

    art = df["art_coverage_pct"]
    df["meets_90_90_90_art"] = np.where(art.isna(), np.nan,
                                        (art >= targets["unaids_90_90_90_art_pct"]).astype(float))
    df["meets_95_95_95_art"] = np.where(art.isna(), np.nan,
                                        (art >= targets["unaids_95_95_95_art_pct"]).astype(float))

    grouped = df.groupby("country_code", group_keys=False)
    for col in ("hiv_prevalence_pct", "art_coverage_pct", "hiv_incidence_per1000",
                "plhiv_count", "health_exp_pct_gdp"):
        df[f"{col}_yoy_change"] = grouped[col].diff().round(4)
    return df


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per country with latest non-null values and change since 2015."""
    value_cols = ["hiv_prevalence_pct", "art_coverage_pct", "hiv_incidence_per1000",
                  "plhiv_count", "health_exp_pct_gdp", "treatment_gap"]
    records = []
    for code, grp in df.groupby("country_code"):
        grp = grp.sort_values("year")
        rec: dict[str, Any] = {
            "country_code": code,
            "country_name": grp["country_name"].iloc[0],
            "iso3": grp["iso3"].iloc[0],
            "region": grp["region"].iloc[0],
            "subregion": grp["subregion"].iloc[0],
        }
        for col in value_cols:
            valid = grp.dropna(subset=[col])
            if valid.empty:
                rec[f"latest_{col}"] = np.nan
                rec[f"latest_{col}_year"] = pd.NA
                continue
            rec[f"latest_{col}"] = valid[col].iloc[-1]
            rec[f"latest_{col}_year"] = int(valid["year"].iloc[-1])
        for col in ("hiv_prevalence_pct", "art_coverage_pct", "hiv_incidence_per1000"):
            base = grp.loc[grp["year"] == 2015, col]
            latest = rec[f"latest_{col}"]
            rec[f"{col}_change_since_2015"] = (
                round(latest - base.iloc[0], 4) if len(base) and pd.notna(base.iloc[0])
                and pd.notna(latest) else np.nan)
        art = rec["latest_art_coverage_pct"]
        rec["meets_90_90_90_art"] = np.nan if pd.isna(art) else float(art >= 81.0)
        rec["has_hiv_data"] = bool(grp["hiv_prevalence_pct"].notna().any())
        records.append(rec)
    summary = pd.DataFrame(records)
    return summary.sort_values("latest_hiv_prevalence_pct", ascending=False,
                               na_position="last").reset_index(drop=True)


def transform(config: dict[str, Any] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the full transformation and write processed CSVs."""
    config = config or load_config()
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    frames = [read_indicator(raw_dir, ind["key"], ind["value_column"])
              for ind in config["indicators"]]

    merged = merge_indicators(frames)
    merged = merged.merge(country_dimension(config), on="country_code", how="left")
    unknown = merged["region"].isna()
    if unknown.any():
        logger.warning("Dropping %d rows for unconfigured countries: %s", unknown.sum(),
                       sorted(merged.loc[unknown, "country_code"].unique()))
        merged = merged[~unknown]
    merged = merged[merged["year"] >= int(config["pipeline"].get("min_year", 2000))]

    # Drop country-years where every indicator is null.
    value_cols = [ind["value_column"] for ind in config["indicators"]]
    merged = merged.dropna(subset=value_cols, how="all")

    processed = add_derived_metrics(merged, config)
    ordered = ["country_code", "iso3", "country_name", "region", "subregion", "year"]
    processed = processed[ordered + [c for c in processed.columns if c not in ordered]]
    processed["year"] = processed["year"].astype(int)
    summary = build_summary(processed)

    out_processed = PROJECT_ROOT / config["paths"]["processed_file"]
    out_summary = PROJECT_ROOT / config["paths"]["summary_file"]
    out_processed.parent.mkdir(parents=True, exist_ok=True)
    processed.to_csv(out_processed, index=False)
    summary.to_csv(out_summary, index=False)
    logger.info("Wrote %s (%d rows x %d cols)", out_processed.name, *processed.shape)
    logger.info("Wrote %s (%d rows)", out_summary.name, len(summary))
    return processed, summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Transform raw HIV/AIDS indicator data.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)
    setup_logging(args.log_level)
    try:
        transform(load_config(args.config))
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
