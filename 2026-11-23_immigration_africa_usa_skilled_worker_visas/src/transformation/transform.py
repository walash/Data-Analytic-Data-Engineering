"""Transformation stage of the Africa-USA skilled worker migration pipeline.

Reads ``data/raw/africa_migration_indicators.csv`` (one row per country-year)
and produces analysis-ready datasets:

* ``data/processed/africa_migration_processed.csv`` - cleaned country-year
  panel with imputation flags, min-max normalised indicators and derived
  metrics (``*_norm`` columns are winsorised at the 5th/95th percentile and
  min-max scaled to 0-1 across the whole panel):

  - ``remittances_per_capita``     remittances (US$) / population
  - ``net_migration_rate_per_1000`` net migration per 1,000 residents
  - ``emigration_rate_per_1000``   net outflow per 1,000 (0 for net receivers)
  - ``migration_intensity_index``  0-100, weighted blend of |net migration
    rate| and remittance dependence (% of GDP)
  - ``skilled_worker_proxy_score`` 0-100, weighted blend of tertiary
    enrollment, remittances per capita and emigration rate - a proxy for the
    size of the educated diaspora pool feeding skilled-visa channels such as
    the US H-1B programme

* ``data/processed/migration_summary.csv`` - one row per country with
  period aggregates (cumulative remittances, CAGR, averages, latest values).
* ``data/processed/usa_net_migration.csv`` - USA net migration series used as
  the destination-side benchmark.

Cleaning rules: duplicate country-years are dropped, numeric columns are
coerced, interior gaps are linearly interpolated per country, and tertiary
enrollment (reported sparsely by UNESCO) is additionally forward/back filled
per country. Every imputed value is flagged in an ``<column>_imputed`` column.
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
NUMERIC_COLUMNS = [
    "net_migration",
    "remittances_pct_gdp",
    "remittances_usd",
    "gdp_per_capita",
    "tertiary_enrollment",
    "population",
]
EDGE_FILL_COLUMNS = ["tertiary_enrollment"]

logger = logging.getLogger("transformation")


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var takes priority)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(config: dict[str, Any]) -> None:
    """Configure root logging from config, honouring the LOG_LEVEL env var."""
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
        datefmt=log_cfg.get("datefmt"),
    )


def resolve(path: str) -> Path:
    """Resolve a config path relative to the project root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def min_max(series: pd.Series, clip_quantiles: tuple[float, float] = (0.05, 0.95)) -> pd.Series:
    """Winsorise at the given quantiles, then scale to [0, 1].

    Clipping stops single-year outliers (e.g. South Africa's 2016 net
    migration estimate) from compressing every other observation towards 0.
    Constant or empty series map to 0.
    """
    lo, hi = series.quantile(clip_quantiles[0]), series.quantile(clip_quantiles[1])
    series = series.clip(lower=lo, upper=hi)
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(np.where(series.notna(), 0.0, np.nan), index=series.index)
    return (series - lo) / (hi - lo)


def read_raw(path: Path) -> pd.DataFrame:
    """Read the combined raw CSV and validate its schema."""
    if not path.exists():
        raise FileNotFoundError(f"Raw input not found: {path} (run the ingestion stage first)")
    df = pd.read_csv(path)
    missing = {"country", "iso3", "year", *NUMERIC_COLUMNS} - set(df.columns)
    if missing:
        raise ValueError(f"Raw CSV is missing columns: {sorted(missing)}")
    logger.info("Read %s: %d rows", path, len(df))
    return df


def clean(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Standardise names, coerce types, de-duplicate and impute gaps per country."""
    countries = {c["iso3"]: c for c in config["pipeline"]["countries"]}
    out = df.copy()
    out["iso3"] = out["iso3"].astype(str).str.strip().str.upper()
    out = out[out["iso3"].isin(countries)]
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out = out.dropna(subset=["year"])
    out["year"] = out["year"].astype(int)
    start, end = config["pipeline"]["start_year"], config["pipeline"]["end_year"]
    out = out[out["year"].between(start, end)]

    before = len(out)
    out = out.drop_duplicates(subset=["iso3", "year"], keep="last")
    if before != len(out):
        logger.warning("Dropped %d duplicate country-year rows", before - len(out))

    out["country"] = out["iso3"].map(lambda code: countries[code]["name"])
    out["subregion"] = out["iso3"].map(lambda code: countries[code]["subregion"])
    for col in NUMERIC_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # Negative values are impossible for these indicators -> treat as missing.
    for col in ["remittances_pct_gdp", "remittances_usd", "gdp_per_capita", "tertiary_enrollment", "population"]:
        out.loc[out[col] < 0, col] = np.nan

    out = out.sort_values(["iso3", "year"]).reset_index(drop=True)
    for col in NUMERIC_COLUMNS:
        was_null = out[col].isna()
        out[col] = out.groupby("iso3")[col].transform(
            lambda s: s.interpolate(method="linear", limit_area="inside")
        )
        if col in EDGE_FILL_COLUMNS:
            out[col] = out.groupby("iso3")[col].transform(lambda s: s.ffill().bfill())
        out[f"{col}_imputed"] = was_null & out[col].notna()
        logger.info("%-20s imputed=%3d remaining_nulls=%3d", col,
                    int(out[f"{col}_imputed"].sum()), int(out[col].isna().sum()))
    return out


def add_derived(df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    """Add per-capita metrics, normalised columns and composite indices."""
    out = df.copy()
    out["remittances_per_capita"] = out["remittances_usd"] / out["population"]
    out["net_migration_rate_per_1000"] = out["net_migration"] / out["population"] * 1000
    out["emigration_rate_per_1000"] = (-out["net_migration_rate_per_1000"]).clip(lower=0)
    out["is_net_emigration"] = out["net_migration"] < 0
    out["remittances_usd_billions"] = out["remittances_usd"] / 1e9

    for col in NUMERIC_COLUMNS + ["remittances_per_capita", "emigration_rate_per_1000"]:
        out[f"{col}_norm"] = min_max(out[col])
    abs_rate_norm = min_max(out["net_migration_rate_per_1000"].abs())

    mi_w = config["pipeline"]["migration_intensity_weights"]
    out["migration_intensity_index"] = 100 * (
        mi_w["net_migration_rate"] * abs_rate_norm
        + mi_w["remittances_pct_gdp"] * out["remittances_pct_gdp_norm"]
    ) / sum(mi_w.values())

    sw_w = config["pipeline"]["skilled_worker_proxy_weights"]
    out["skilled_worker_proxy_score"] = 100 * (
        sw_w["tertiary_enrollment"] * out["tertiary_enrollment_norm"]
        + sw_w["remittances_per_capita"] * out["remittances_per_capita_norm"]
        + sw_w["emigration_rate"] * out["emigration_rate_per_1000_norm"]
    ) / sum(sw_w.values())

    out["remittances_usd_yoy_pct"] = out.groupby("iso3")["remittances_usd"].pct_change(fill_method=None) * 100
    out["net_migration_yoy_change"] = out.groupby("iso3")["net_migration"].diff()
    out["data_complete"] = out[NUMERIC_COLUMNS].notna().all(axis=1)
    return out.round(6)


def _cagr(first: float, last: float, years: int) -> float:
    """Compound annual growth rate in percent, NaN if undefined."""
    if years <= 0 or pd.isna(first) or pd.isna(last) or first <= 0 or last <= 0:
        return np.nan
    return ((last / first) ** (1 / years) - 1) * 100


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the panel to one row per country."""
    rows = []
    for iso3, grp in df.groupby("iso3", sort=False):
        grp = grp.sort_values("year")
        rem = grp.dropna(subset=["remittances_usd"])
        latest = grp.iloc[-1]
        rows.append({
            "iso3": iso3,
            "country": latest["country"],
            "subregion": latest["subregion"],
            "first_year": int(grp["year"].min()),
            "last_year": int(grp["year"].max()),
            "total_net_migration": grp["net_migration"].sum(min_count=1),
            "avg_net_migration": grp["net_migration"].mean(),
            "avg_net_migration_rate_per_1000": grp["net_migration_rate_per_1000"].mean(),
            "net_emigration_years": int(grp["is_net_emigration"].sum()),
            "cumulative_remittances_usd": grp["remittances_usd"].sum(min_count=1),
            "avg_remittances_pct_gdp": grp["remittances_pct_gdp"].mean(),
            "remittances_cagr_pct": _cagr(rem["remittances_usd"].iloc[0], rem["remittances_usd"].iloc[-1],
                                          int(rem["year"].iloc[-1] - rem["year"].iloc[0])) if len(rem) > 1 else np.nan,
            "latest_remittances_usd": latest["remittances_usd"],
            "latest_remittances_per_capita": latest["remittances_per_capita"],
            "latest_gdp_per_capita": latest["gdp_per_capita"],
            "avg_tertiary_enrollment": grp["tertiary_enrollment"].mean(),
            "latest_population": latest["population"],
            "avg_migration_intensity_index": grp["migration_intensity_index"].mean(),
            "avg_skilled_worker_proxy_score": grp["skilled_worker_proxy_score"].mean(),
        })
    summary = pd.DataFrame(rows).sort_values("avg_skilled_worker_proxy_score", ascending=False)
    summary.insert(3, "skilled_worker_rank", range(1, len(summary) + 1))
    return summary.round(4).reset_index(drop=True)


def parse_usa(path: Path) -> pd.DataFrame:
    """Parse the World Bank USA net migration JSON into a year/value frame."""
    if not path.exists():
        logger.warning("USA benchmark file missing: %s", path)
        return pd.DataFrame(columns=["year", "usa_net_migration"])
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    records = payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []
    usa = pd.DataFrame(
        [{"year": int(r["date"]), "usa_net_migration": r["value"]} for r in records]
    )
    if usa.empty:
        return pd.DataFrame(columns=["year", "usa_net_migration"])
    return usa.sort_values("year").reset_index(drop=True)


def transform(config: dict[str, Any]) -> dict[str, Path]:
    """Run the transformation stage and return the written file paths."""
    paths = config["paths"]
    processed_dir = resolve(paths["processed_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)

    panel = add_derived(clean(read_raw(resolve(paths["combined_raw_csv"])), config), config)
    summary = build_summary(panel)
    usa_file = resolve(paths["raw_dir"]) / config["world_bank_api"]["usa_indicator"]["raw_file"]
    usa = parse_usa(usa_file)

    outputs = {
        "processed": resolve(paths["processed_csv"]),
        "summary": resolve(paths["summary_csv"]),
        "usa": resolve(paths["usa_csv"]),
    }
    panel.to_csv(outputs["processed"], index=False)
    summary.to_csv(outputs["summary"], index=False)
    usa.to_csv(outputs["usa"], index=False)
    logger.info("Wrote %s (%d rows x %d cols)", outputs["processed"], *panel.shape)
    logger.info("Wrote %s (%d rows)", outputs["summary"], len(summary))
    logger.info("Wrote %s (%d rows)", outputs["usa"], len(usa))
    return outputs


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Transform raw migration indicators")
    parser.add_argument("--config", help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    setup_logging(config)
    try:
        transform(config)
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
