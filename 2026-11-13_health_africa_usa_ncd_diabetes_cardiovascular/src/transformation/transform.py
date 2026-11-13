"""Transformation stage: clean, normalise and enrich raw World Bank NCD data.

Input  (``data/raw/``):  one CSV per indicator produced by ``ingest.py``.
Output (``data/processed/``):

* ``ncd_observations.csv`` - tidy long fact table (one row per
  country x indicator x year) enriched with region tags and change metrics
* ``countries.csv``        - country / aggregate dimension
* ``indicators.csv``       - indicator dimension
* ``africa_usa_ncd_wide.csv`` - Africa + USA + benchmarks pivoted to one column
  per indicator, convenient for notebooks and BI tools

Cleaning rules:

1. Strip whitespace, upper-case ISO3 codes, cast ``year`` to int and
   ``value`` to float (non-numeric -> NaN).
2. Drop rows without ISO3, year or value (logged counts).
3. Drop out-of-range values (percentages outside 0-100) and years before
   ``pipeline.min_year``.
4. Deduplicate on (iso3, indicator_code, year), keeping the last record.
5. Tag each entity as ``Africa`` / ``USA`` / ``Benchmark`` / ``Aggregate`` /
   ``Other`` and attach the African sub-region.
6. Compute change versus the previous available year and an Africa-relative
   rank per indicator-year.
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
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("ncd.transform")


def setup_logging(level: str = "INFO") -> None:
    """Configure logging with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load YAML configuration (``$CONFIG_PATH`` or ``config/config.yaml``)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve(path_str: str) -> Path:
    """Resolve a config path relative to the project root."""
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_raw_indicators(config: Dict[str, Any], raw_dir: Path) -> pd.DataFrame:
    """Read every configured indicator CSV that exists in ``raw_dir``.

    Missing files are logged and skipped so a partial ingestion does not block
    the pipeline. Raises ``FileNotFoundError`` if no file is found at all.
    """
    frames = []
    for key, meta in config["worldbank"]["indicators"].items():
        csv_path = raw_dir / f"{key}.csv"
        if not csv_path.exists():
            logger.warning("Raw file not found for %s (%s); skipping", key, csv_path)
            continue
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=True)
        df["indicator_key"] = key
        df["indicator_code_cfg"] = meta["code"]
        logger.info("Read %d rows from %s", len(df), csv_path.name)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No raw indicator CSVs found in {raw_dir}")
    return pd.concat(frames, ignore_index=True)


def clean(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Apply type casting, null handling, range checks and deduplication."""
    pipeline_cfg = config.get("pipeline", {})
    out = df.copy()
    for col in ("country_id", "country_name", "iso3", "indicator_id", "indicator_name"):
        out[col] = out[col].fillna("").astype(str).str.strip()
    out["iso3"] = out["iso3"].str.upper()
    # Fall back to the configured code if the indicator id column is blank
    out["indicator_code"] = out["indicator_id"].where(
        out["indicator_id"] != "", out["indicator_code_cfg"]
    )
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")

    before = len(out)
    out = out[(out["iso3"] != "") & out["year"].notna() & out["value"].notna()]
    logger.info("Dropped %d rows with missing iso3/year/value", before - len(out))

    before = len(out)
    max_pct = float(pipeline_cfg.get("max_valid_percentage", 100.0))
    min_year = int(pipeline_cfg.get("min_year", 2000))
    out = out[(out["value"] >= 0) & (out["value"] <= max_pct) & (out["year"] >= min_year)]
    logger.info("Dropped %d rows out of valid range / year window", before - len(out))

    out["year"] = out["year"].astype(int)
    out["value"] = out["value"].astype(float).round(4)

    before = len(out)
    out = out.drop_duplicates(subset=["iso3", "indicator_code", "year"], keep="last")
    logger.info("Removed %d duplicate rows", before - len(out))

    return out[
        ["country_id", "country_name", "iso3", "indicator_key", "indicator_code",
         "indicator_name", "year", "value"]
    ].reset_index(drop=True)


def build_region_lookup(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Create an ISO3 -> {region_group, africa_subregion, is_aggregate} map."""
    regions = config["regions"]
    lookup: Dict[str, Dict[str, Any]] = {}
    for code in regions.get("worldbank_aggregates", []):
        lookup[code] = {"region_group": "Aggregate", "africa_subregion": None, "is_aggregate": 1}
    for code in regions.get("benchmark_aggregates", []):
        lookup[code] = {"region_group": "Benchmark", "africa_subregion": None, "is_aggregate": 1}
    for subregion, codes in regions.get("africa_subregions", {}).items():
        for code in codes:
            lookup[code] = {
                "region_group": "Africa", "africa_subregion": subregion, "is_aggregate": 0,
            }
    lookup[regions.get("usa_iso3", "USA")] = {
        "region_group": "USA", "africa_subregion": None, "is_aggregate": 0,
    }
    return lookup


def enrich(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Add region tags, change-vs-previous-year metrics and Africa ranks."""
    lookup = build_region_lookup(config)
    default = {"region_group": "Other", "africa_subregion": None, "is_aggregate": 0}
    tags = df["iso3"].map(lambda c: lookup.get(c, default)).apply(pd.Series)
    out = pd.concat([df, tags], axis=1)
    out["is_aggregate"] = out["is_aggregate"].astype(int)

    out = out.sort_values(["iso3", "indicator_code", "year"]).reset_index(drop=True)
    grp = out.groupby(["iso3", "indicator_code"], sort=False)
    out["prev_year"] = grp["year"].shift(1)
    out["prev_value"] = grp["value"].shift(1)
    out["change_pp"] = (out["value"] - out["prev_value"]).round(4)
    years_gap = out["year"] - out["prev_year"]
    out["change_pp_per_year"] = (out["change_pp"] / years_gap.replace(0, np.nan)).round(4)
    out["is_latest"] = (out["year"] == grp["year"].transform("max")).astype(int)

    africa = out["region_group"] == "Africa"
    out["africa_rank"] = np.nan
    out.loc[africa, "africa_rank"] = (
        out[africa].groupby(["indicator_code", "year"])["value"]
        .rank(method="min", ascending=False)
    )
    out["prev_year"] = out["prev_year"].astype("Int64")
    out["africa_rank"] = out["africa_rank"].astype("Int64")
    return out


def build_dimensions(obs: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Derive the country and indicator dimension tables from the fact table."""
    countries = (
        obs.sort_values("year")
        .groupby("iso3", as_index=False)
        .agg(
            country_id=("country_id", "last"),
            country_name=("country_name", "last"),
            region_group=("region_group", "first"),
            africa_subregion=("africa_subregion", "first"),
            is_aggregate=("is_aggregate", "first"),
        )
        .sort_values("iso3")
    )
    indicators = (
        obs.groupby(["indicator_key", "indicator_code"], as_index=False)
        .agg(indicator_name=("indicator_name", "last"),
             first_year=("year", "min"), last_year=("year", "max"),
             n_observations=("value", "size"))
        .sort_values("indicator_key")
    )
    return {"countries": countries, "indicators": indicators}


def build_focus_wide(obs: pd.DataFrame) -> pd.DataFrame:
    """Pivot Africa, USA and benchmark rows to one column per indicator."""
    focus = obs[obs["region_group"].isin(["Africa", "USA", "Benchmark"])]
    wide = focus.pivot_table(
        index=["iso3", "country_name", "region_group", "year"],
        columns="indicator_key",
        values="value",
        aggfunc="last",
    ).reset_index()
    wide.columns.name = None
    subregions = focus.drop_duplicates("iso3").set_index("iso3")["africa_subregion"]
    wide.insert(3, "africa_subregion", wide["iso3"].map(subregions))
    return wide.sort_values(["region_group", "iso3", "year"]).reset_index(drop=True)


def transform(config: Dict[str, Any]) -> Dict[str, Path]:
    """Run the transformation stage end-to-end and write processed outputs.

    Returns:
        Mapping of output name -> written file path.
    """
    raw_dir = resolve(config["paths"]["raw_dir"])
    processed_dir = resolve(config["paths"]["processed_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)

    raw = read_raw_indicators(config, raw_dir)
    cleaned = clean(raw, config)
    obs = enrich(cleaned, config)
    dims = build_dimensions(obs)
    wide = build_focus_wide(obs)

    outputs = {
        "ncd_observations": obs,
        "countries": dims["countries"],
        "indicators": dims["indicators"],
        "africa_usa_ncd_wide": wide,
    }
    written: Dict[str, Path] = {}
    for name, frame in outputs.items():
        path = processed_dir / f"{name}.csv"
        frame.to_csv(path, index=False)
        written[name] = path
        logger.info("Wrote %d rows -> %s", len(frame), path)

    summary = obs.groupby(["indicator_key", "region_group"]).size().unstack(fill_value=0)
    logger.info("Observation counts by indicator/region:\n%s", summary.to_string())
    return written


def main() -> int:
    """CLI entry point."""
    config = load_config()
    setup_logging(os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")))
    try:
        transform(config)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
