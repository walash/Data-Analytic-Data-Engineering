"""Transformation module for the Maternal & Child Health (Africa vs USA) pipeline.

Reads the per-indicator raw CSVs in ``data/raw/``, then:

1. Validates schema and casts types (year -> int, value -> float).
2. Standardises country names/codes against ``config.yaml`` and drops
   countries outside the study scope.
3. Removes duplicates (keeping the last observation per country/year/indicator)
   and nulls out values outside plausible ranges.
4. Builds a tidy long table and pivots it to a wide country-year table.
5. Fills short within-country gaps via linear interpolation (no extrapolation)
   and records how many values were imputed per row.
6. Enriches with region (Africa / USA), sub-region, derived ratios and
   SDG 3.1/3.2 target flags.
7. Writes processed outputs to ``data/processed/``:
     - maternal_child_health_long.csv
     - maternal_child_health_wide.csv
     - country_metadata.csv
     - yearly_summary.csv

Usage:
    python src/transformation/transform.py
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
REQUIRED_COLUMNS = {"country", "country_code", "year", "value", "indicator"}

# SDG targets for 2030
SDG_MMR_TARGET = 70.0    # SDG 3.1: MMR < 70 per 100,000 live births
SDG_U5MR_TARGET = 25.0   # SDG 3.2: U5MR <= 25 per 1,000 live births
SDG_NMR_TARGET = 12.0    # SDG 3.2: NMR <= 12 per 1,000 live births

logger = logging.getLogger("transform")


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Load YAML configuration (``CONFIG_PATH`` env var overrides ``path``)."""
    cfg_path = Path(os.environ.get("CONFIG_PATH", path))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_dir(env_var: str, configured: str) -> Path:
    """Resolve a directory from an env var or config value relative to project root."""
    path = Path(os.environ.get(env_var, configured))
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_raw_indicator(path: Path, indicator_name: str) -> pd.DataFrame:
    """Read and type-cast one raw indicator CSV.

    Args:
        path: CSV path.
        indicator_name: Expected indicator short name (used if column missing/blank).

    Returns:
        DataFrame with columns country, country_code, year, value, indicator.
    """
    df = pd.read_csv(path, dtype={"country": "string", "country_code": "string"})
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    df["indicator"] = df["indicator"].fillna(indicator_name).astype("string")
    df["country_code"] = df["country_code"].str.strip().str.upper()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["country_code", "year", "value"])
    if len(df) < before:
        logger.info("%s: dropped %d rows with null keys/values", path.name, before - len(df))
    df["year"] = df["year"].astype(int)
    df["value"] = df["value"].astype(float)
    return df[["country", "country_code", "year", "value", "indicator"]]


def load_raw(raw_dir: Path, indicators: Dict[str, Any]) -> pd.DataFrame:
    """Load all configured raw indicator files into a single long DataFrame."""
    frames: List[pd.DataFrame] = []
    for name in indicators:
        path = raw_dir / f"{name}.csv"
        if not path.exists():
            logger.warning("Raw file missing, skipping: %s", path)
            continue
        frame = read_raw_indicator(path, name)
        logger.info("Loaded %-40s %4d rows", path.name, len(frame))
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No raw indicator files found in {raw_dir}")
    return pd.concat(frames, ignore_index=True)


def country_metadata(config: Dict[str, Any]) -> pd.DataFrame:
    """Build the country metadata table from configuration."""
    meta = pd.DataFrame(config["countries"]).rename(
        columns={"iso3": "country_code", "iso2": "iso2_code", "name": "country_name"}
    )
    meta["is_africa"] = (meta["region"] == "Africa").astype(int)
    return meta[["country_code", "iso2_code", "country_name", "region", "subregion", "is_africa"]]


def clean_long(df: pd.DataFrame, config: Dict[str, Any], meta: pd.DataFrame) -> pd.DataFrame:
    """Standardise, filter, deduplicate and range-validate the long table."""
    in_scope = set(meta["country_code"])
    out_of_scope = sorted(set(df["country_code"]) - in_scope)
    if out_of_scope:
        logger.warning("Dropping out-of-scope country codes: %s", out_of_scope)
    df = df[df["country_code"].isin(in_scope)].copy()

    pipe = config["pipeline"]
    df = df[df["year"].between(int(pipe["year_min"]), int(pipe["year_max"]))]

    before = len(df)
    df = df.drop_duplicates(subset=["country_code", "year", "indicator"], keep="last")
    if len(df) < before:
        logger.info("Removed %d duplicate observations", before - len(df))

    for indicator, (lo, hi) in pipe.get("valid_ranges", {}).items():
        mask = (df["indicator"] == indicator) & ~df["value"].between(lo, hi)
        if mask.any():
            logger.warning("Nulling %d out-of-range values for %s", int(mask.sum()), indicator)
            df.loc[mask, "value"] = np.nan
    df = df.dropna(subset=["value"])

    # Replace API country labels (e.g. "Egypt, Arab Rep.") with canonical names.
    names = meta.set_index("country_code")["country_name"]
    df["country"] = df["country_code"].map(names)
    df = df.merge(meta[["country_code", "region"]], on="country_code", how="left")
    return df.sort_values(["country_code", "indicator", "year"]).reset_index(drop=True)


def pivot_wide(long_df: pd.DataFrame, meta: pd.DataFrame, indicators: List[str],
               year_min: int, year_max: int) -> pd.DataFrame:
    """Pivot to one row per country-year on a complete country x year grid."""
    wide = long_df.pivot_table(
        index=["country_code", "year"], columns="indicator", values="value", aggfunc="mean"
    )
    grid = pd.MultiIndex.from_product(
        [sorted(meta["country_code"]), range(year_min, year_max + 1)],
        names=["country_code", "year"],
    )
    wide = wide.reindex(grid)
    for col in indicators:
        if col not in wide.columns:
            wide[col] = np.nan
    wide = wide[indicators].reset_index()
    wide.columns.name = None
    return wide


def interpolate_gaps(wide: pd.DataFrame, indicators: List[str], limit: int) -> pd.DataFrame:
    """Linearly interpolate interior gaps within each country's series.

    Only gaps bounded on both sides by observations are filled (``limit_area='inside'``),
    so no values are extrapolated beyond the observed range.
    """
    wide = wide.sort_values(["country_code", "year"]).copy()
    was_null = wide[indicators].isna()
    wide[indicators] = wide.groupby("country_code")[indicators].transform(
        lambda s: s.interpolate(method="linear", limit=limit, limit_area="inside")
    )
    wide["imputed_values_count"] = (was_null & wide[indicators].notna()).sum(axis=1).astype(int)
    logger.info("Interpolated %d values", int(wide["imputed_values_count"].sum()))
    return wide


def enrich(wide: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """Add region metadata, derived metrics and SDG target flags."""
    wide = wide.merge(
        meta[["country_code", "country_name", "region", "subregion"]], on="country_code", how="left"
    )
    u5 = wide["under5_mortality_rate"]
    nmr = wide["neonatal_mortality_rate"]
    wide["neonatal_share_of_under5_pct"] = np.where(u5 > 0, (nmr / u5) * 100, np.nan).round(2)
    wide["post_neonatal_under5_rate"] = (u5 - nmr).round(2)

    def flag(series: pd.Series, target: float, strict: bool) -> pd.Series:
        hit = series < target if strict else series <= target
        return hit.astype("Int64").where(series.notna())

    wide["meets_sdg_mmr"] = flag(wide["maternal_mortality_ratio"], SDG_MMR_TARGET, strict=True)
    wide["meets_sdg_u5mr"] = flag(u5, SDG_U5MR_TARGET, strict=False)
    wide["meets_sdg_nmr"] = flag(nmr, SDG_NMR_TARGET, strict=False)

    wide = wide.sort_values(["country_code", "year"])
    wide["mmr_yoy_change_pct"] = (
        wide.groupby("country_code")["maternal_mortality_ratio"].pct_change(fill_method=None) * 100
    ).round(2)
    wide["u5mr_yoy_change_pct"] = (
        wide.groupby("country_code")["under5_mortality_rate"].pct_change(fill_method=None) * 100
    ).round(2)

    front = ["country_code", "country_name", "region", "subregion", "year"]
    rest = [c for c in wide.columns if c not in front]
    return wide[front + rest].reset_index(drop=True)


def yearly_summary(wide: pd.DataFrame, indicators: List[str]) -> pd.DataFrame:
    """Aggregate indicators by region and year (mean, plus Africa median MMR)."""
    agg = wide.groupby(["region", "year"])[indicators].mean().round(2)
    agg.columns = [f"avg_{c}" for c in agg.columns]
    counts = wide.groupby(["region", "year"])["maternal_mortality_ratio"].agg(
        countries_reporting_mmr="count", median_maternal_mortality_ratio="median"
    )
    out = agg.join(counts).reset_index()
    out["countries_reporting_mmr"] = out["countries_reporting_mmr"].astype(int)
    return out.sort_values(["region", "year"]).reset_index(drop=True)


def transform(config: Dict[str, Any]) -> Dict[str, Path]:
    """Run the full transformation step and return the output file paths."""
    raw_dir = resolve_dir("RAW_DATA_DIR", config["paths"]["raw_dir"])
    out_dir = resolve_dir("PROCESSED_DATA_DIR", config["paths"]["processed_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    pipe = config["pipeline"]
    indicators = list(config["indicators"].keys())

    meta = country_metadata(config)
    long_df = clean_long(load_raw(raw_dir, config["indicators"]), config, meta)
    year_min = int(long_df["year"].min())
    year_max = int(long_df["year"].max())

    wide = pivot_wide(long_df, meta, indicators, year_min, year_max)
    if pipe.get("interpolate_gaps", True):
        wide = interpolate_gaps(wide, indicators, int(pipe.get("interpolation_limit_years", 3)))
    else:
        wide["imputed_values_count"] = 0
    # Drop country-years with no data at all after interpolation.
    wide = wide.dropna(subset=indicators, how="all")
    wide = enrich(wide, meta)
    summary = yearly_summary(wide, indicators)

    outputs = {
        "long": out_dir / "maternal_child_health_long.csv",
        "wide": out_dir / "maternal_child_health_wide.csv",
        "country_metadata": out_dir / "country_metadata.csv",
        "yearly_summary": out_dir / "yearly_summary.csv",
    }
    long_df.to_csv(outputs["long"], index=False)
    wide.to_csv(outputs["wide"], index=False)
    meta.to_csv(outputs["country_metadata"], index=False)
    summary.to_csv(outputs["yearly_summary"], index=False)
    logger.info(
        "Wrote long=%d, wide=%d, metadata=%d, summary=%d rows to %s",
        len(long_df), len(wide), len(meta), len(summary), out_dir,
    )
    return outputs


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Transform raw maternal & child health data.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.yaml")
    args = parser.parse_args(argv)
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    try:
        transform(load_config(Path(args.config)))
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
