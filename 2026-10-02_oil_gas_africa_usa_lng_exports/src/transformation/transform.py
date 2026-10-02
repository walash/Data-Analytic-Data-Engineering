"""Transformation stage of the Africa LNG Exports to the USA pipeline.

Reads the two raw CSVs produced by ``src/ingestion/ingest.py`` and builds
analysis-ready tables in ``data/processed/``:

* ``lng_trade_clean.csv``      - every Comtrade row, typed, de-duplicated,
  with numeric partner codes resolved to country names.
* ``lng_exports_pivot.csv``    - total LNG export value (USD bn) by country
  (rows) and year (columns).
* ``wb_indicators_wide.csv``   - World Bank indicators pivoted to one row per
  country-year with one column per indicator.
* ``lng_exports_processed.csv`` - one row per exporter-year combining export
  totals, USA-bound exports, destination diversity, YoY growth, World Bank
  indicators and the derived ``export_value_billion_usd`` and
  ``gas_dependency_score`` metrics.

Comtrade reports a ``World`` (partner_code 0) aggregate next to bilateral
rows; totals use the ``World`` row when present (falling back to the sum of
bilateral rows) so values are never double counted.

Usage::

    python src/transformation/transform.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("transform")

# UN M49 codes that the Comtrade preview API returns without a description.
M49_PARTNER_NAMES: Dict[int, str] = {
    0: "World", 32: "Argentina", 76: "Brazil", 108: "Burundi",
    124: "Canada", 148: "Chad", 170: "Colombia", 174: "Comoros",
    178: "Congo", 180: "DR Congo", 191: "Croatia", 214: "Dominican Republic",
    300: "Greece", 376: "Israel", 388: "Jamaica", 400: "Jordan",
    454: "Malawi", 458: "Malaysia", 470: "Malta", 484: "Mexico",
    490: "Other Asia, nes", 562: "Niger", 568: "Other Europe, nes",
    591: "Panama", 608: "Philippines", 616: "Poland", 646: "Rwanda",
    764: "Thailand", 784: "United Arab Emirates", 800: "Uganda",
    842: "USA", 894: "Zambia", 899: "Areas, nes",
}


def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var wins)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def get_paths(config: Dict[str, Any]) -> Dict[str, Path]:
    """Resolve input and output paths and make sure output dirs exist."""
    raw = PROJECT_ROOT / config["paths"]["raw_data_dir"]
    processed = PROJECT_ROOT / config["paths"]["processed_data_dir"]
    processed.mkdir(parents=True, exist_ok=True)
    return {
        "comtrade": raw / config["paths"]["comtrade_file"],
        "worldbank": raw / config["paths"]["worldbank_file"],
        "trade_clean": processed / "lng_trade_clean.csv",
        "pivot": processed / "lng_exports_pivot.csv",
        "wb_wide": processed / "wb_indicators_wide.csv",
        "exports": processed / "lng_exports_processed.csv",
    }


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def clean_trade(raw: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Type-cast, de-duplicate and enrich the raw Comtrade table."""
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("reporter", "partner", "flow", "commodity"):
        df[col] = df[col].astype("string").str.strip()
    df["flow"] = df["flow"].str.upper()
    for col in ("year", "reporter_code", "partner_code"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("trade_value_usd", "qty_kg"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["year", "reporter_code", "partner_code", "flow",
                           "trade_value_usd"])
    df = df[df["flow"].isin(["X", "M", "RX"]) & (df["trade_value_usd"] >= 0)]
    df["qty_kg"] = df["qty_kg"].fillna(0.0).clip(lower=0)
    df[["year", "reporter_code", "partner_code"]] = (
        df[["year", "reporter_code", "partner_code"]].astype(int)
    )

    # Resolve names: reporters from config, partners from the M49 lookup.
    reporters = {c["m49"]: c for c in config["countries"]}
    df["reporter"] = df["reporter_code"].map(
        lambda code: reporters.get(code, {}).get("name")
    ).fillna(df["reporter"])
    df["country_code"] = df["reporter_code"].map(
        lambda code: reporters.get(code, {}).get("iso3")
    )
    numeric_partner = df["partner"].str.fullmatch(r"\d+").fillna(True)
    df.loc[numeric_partner, "partner"] = (
        df.loc[numeric_partner, "partner_code"].map(M49_PARTNER_NAMES)
        .fillna(df.loc[numeric_partner, "partner_code"].astype(str))
    )
    df["commodity"] = df["commodity"].fillna(
        config["api"]["comtrade"]["commodity_label"]
    )

    df = df.drop_duplicates(
        subset=["year", "reporter_code", "partner_code", "flow"], keep="last"
    )
    logger.info("Trade cleaning: %d -> %d rows", before, len(df))
    return df.sort_values(["year", "reporter", "flow", "partner_code"]) \
        .reset_index(drop=True)


def clean_worldbank(raw: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Type-cast, de-duplicate and normalise the long World Bank table."""
    df = raw.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    for col in ("country_code", "country", "indicator", "indicator_name"):
        df[col] = df[col].astype("string").str.strip()
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["country_code", "year", "indicator", "value"])
    df["year"] = df["year"].astype(int)
    # Re-derive the short name from config so it is always consistent.
    df["indicator_name"] = df["indicator"].map(config["indicators"]) \
        .fillna(df["indicator_name"])
    names = {c["iso3"]: c["name"] for c in config["countries"]}
    df = df[df["country_code"].isin(names)]
    df["country"] = df["country_code"].map(names)  # e.g. "Egypt, Arab Rep."
    df = df.drop_duplicates(subset=["country_code", "year", "indicator"],
                            keep="last")
    logger.info("World Bank cleaning: %d -> %d rows", before, len(df))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Reshaping and feature engineering
# --------------------------------------------------------------------------- #
def pivot_worldbank(wb: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """Pivot indicators to one row per country-year."""
    wide = wb.pivot_table(index=["country_code", "country", "year"],
                          columns="indicator_name", values="value",
                          aggfunc="mean").reset_index()
    wide.columns.name = None
    for col in config["indicators"].values():
        if col not in wide.columns:
            wide[col] = np.nan
    ordered = ["country_code", "country", "year"] + \
        list(config["indicators"].values())
    return wide[ordered].sort_values(["country_code", "year"]) \
        .reset_index(drop=True)


def summarise_exports(trade: pd.DataFrame,
                      config: Dict[str, Any]) -> pd.DataFrame:
    """Aggregate export flows (flow == 'X') to one row per exporter-year."""
    world_code = config["world_partner_code"]
    usa_code = config["usa_partner_code"]
    exports = trade[trade["flow"] == "X"]
    keys = ["country_code", "reporter_code", "reporter", "year"]

    bilateral = exports[exports["partner_code"] != world_code]
    world = exports[exports["partner_code"] == world_code] \
        .set_index(keys)[["trade_value_usd", "qty_kg"]]
    bilateral_sum = bilateral.groupby(keys)[["trade_value_usd", "qty_kg"]].sum()
    # Prefer the reported World aggregate; fall back to bilateral sums.
    totals = world.combine_first(bilateral_sum).rename(columns={
        "trade_value_usd": "total_export_value_usd",
        "qty_kg": "total_export_qty_kg",
    })

    usa = bilateral[bilateral["partner_code"] == usa_code] \
        .groupby(keys)["trade_value_usd"].sum().rename("usa_export_value_usd")
    n_dest = bilateral[bilateral["trade_value_usd"] > 0] \
        .groupby(keys)["partner_code"].nunique().rename("num_destinations")
    top = bilateral.sort_values("trade_value_usd", ascending=False) \
        .groupby(keys).head(1).set_index(keys)
    top = top[["partner", "trade_value_usd"]].rename(columns={
        "partner": "top_destination",
        "trade_value_usd": "top_destination_value_usd",
    })

    summary = totals.join([usa, n_dest, top], how="left").reset_index()
    summary["usa_export_value_usd"] = summary["usa_export_value_usd"].fillna(0.0)
    summary["num_destinations"] = summary["num_destinations"].fillna(0) \
        .astype(int)
    summary = summary.rename(columns={"reporter": "country"})
    return summary.sort_values(["country", "year"]).reset_index(drop=True)


def build_export_pivot(summary: pd.DataFrame) -> pd.DataFrame:
    """Country x year matrix of total LNG export value in USD billions."""
    pivot = summary.pivot_table(index="country", columns="year",
                                values="total_export_value_usd",
                                aggfunc="sum") / 1e9
    pivot.columns = [str(c) for c in pivot.columns]
    pivot["total_all_years"] = pivot.sum(axis=1, skipna=True)
    pivot = pivot.sort_values("total_all_years", ascending=False).round(4)
    return pivot.reset_index()


def _minmax(series: pd.Series) -> pd.Series:
    """Scale a series to 0-1 (constant or empty series map to 0)."""
    lo, hi = series.min(skipna=True), series.max(skipna=True)
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series(0.0, index=series.index)
    return (series - lo) / (hi - lo)


def fill_recent_indicators(wb_wide: pd.DataFrame, summary: pd.DataFrame,
                           config: Dict[str, Any]) -> pd.DataFrame:
    """Carry each indicator's latest observed value forward a few years.

    World Bank rents series lag by 2-4 years, so an export year without a
    published value (e.g. 2023) reuses the most recent prior observation, up
    to ``pipeline.indicator_ffill_years`` years back.
    """
    limit = int(config["pipeline"].get("indicator_ffill_years", 3))
    years = sorted(set(wb_wide["year"]).union(summary["year"]))
    grid = pd.MultiIndex.from_product(
        [sorted(wb_wide["country_code"].unique()), years],
        names=["country_code", "year"],
    )
    indicators = list(config["indicators"].values())
    filled = wb_wide.set_index(["country_code", "year"])[indicators] \
        .reindex(grid).groupby(level="country_code").ffill(limit=limit)
    return filled.reset_index()


def add_derived_metrics(df: pd.DataFrame,
                        config: Dict[str, Any]) -> pd.DataFrame:
    """Add value conversions, shares, YoY growth and the dependency score."""
    out = df.copy()
    out["export_value_billion_usd"] = out["total_export_value_usd"] / 1e9
    out["usa_export_value_million_usd"] = out["usa_export_value_usd"] / 1e6
    out["usa_share_pct"] = np.where(
        out["total_export_value_usd"] > 0,
        100 * out["usa_export_value_usd"] / out["total_export_value_usd"], 0.0,
    )
    out["export_share_of_africa_pct"] = 100 * out["total_export_value_usd"] / \
        out.groupby("year")["total_export_value_usd"].transform("sum")

    out = out.sort_values(["country_code", "year"])
    prev_value = out.groupby("country_code")["total_export_value_usd"].shift(1)
    prev_year = out.groupby("country_code")["year"].shift(1)
    out["prev_year_available"] = prev_year.astype("Int64")
    min_base = float(config["pipeline"].get("yoy_min_base_usd", 1e7))
    out["yoy_growth_pct"] = np.where(
        prev_value >= min_base,
        100 * (out["total_export_value_usd"] - prev_value) / prev_value,
        np.nan,
    )

    merch = out["merchandise_exports_usd"]
    out["lng_share_of_merch_exports_pct"] = np.where(
        merch > 0, 100 * out["total_export_value_usd"] / merch, np.nan,
    )
    out["lng_share_of_merch_exports_pct"] = \
        out["lng_share_of_merch_exports_pct"].clip(upper=100)

    weights = config["pipeline"]["gas_dependency_weights"]
    score = pd.Series(0.0, index=out.index)
    for column, weight in weights.items():
        score += weight * _minmax(out[column]).fillna(0.0)
    out["gas_dependency_score"] = (100 * score).round(2)
    return out


def build_processed_exports(trade: pd.DataFrame, wb_wide: pd.DataFrame,
                            config: Dict[str, Any]) -> pd.DataFrame:
    """Join export summary with World Bank indicators and derive metrics."""
    summary = summarise_exports(trade, config)
    merged = summary.merge(fill_recent_indicators(wb_wide, summary, config),
                           on=["country_code", "year"], how="left")
    missing = merged["gas_rents_pct_gdp"].isna().sum()
    if missing:
        logger.warning("%d exporter-years have no World Bank match", missing)
    enriched = add_derived_metrics(merged, config)
    columns = [
        "country_code", "reporter_code", "country", "year",
        "total_export_value_usd", "export_value_billion_usd",
        "total_export_qty_kg", "usa_export_value_usd",
        "usa_export_value_million_usd", "usa_share_pct",
        "export_share_of_africa_pct", "num_destinations", "top_destination",
        "top_destination_value_usd", "prev_year_available", "yoy_growth_pct",
        *config["indicators"].values(),
        "lng_share_of_merch_exports_pct", "gas_dependency_score",
    ]
    return enriched[columns].sort_values(["country", "year"]) \
        .reset_index(drop=True)


def transform(config: Dict[str, Any] | None = None) -> Dict[str, Path]:
    """Run the full transformation stage and return written file paths."""
    config = config or load_config()
    paths = get_paths(config)
    for key in ("comtrade", "worldbank"):
        if not paths[key].exists():
            raise FileNotFoundError(
                f"Missing raw file {paths[key]} - run ingestion first"
            )

    trade = clean_trade(pd.read_csv(paths["comtrade"]), config)
    wb = clean_worldbank(pd.read_csv(paths["worldbank"]), config)
    wb_wide = pivot_worldbank(wb, config)
    exports = build_processed_exports(trade, wb_wide, config)
    pivot = build_export_pivot(exports)

    trade.to_csv(paths["trade_clean"], index=False)
    wb_wide.to_csv(paths["wb_wide"], index=False)
    exports.to_csv(paths["exports"], index=False)
    pivot.to_csv(paths["pivot"], index=False)

    logger.info("Clean trade rows: %d -> %s", len(trade), paths["trade_clean"])
    logger.info("WB wide rows: %d -> %s", len(wb_wide), paths["wb_wide"])
    logger.info("Exporter-year rows: %d -> %s", len(exports), paths["exports"])
    logger.info("Pivot rows: %d -> %s", len(pivot), paths["pivot"])
    return {k: v for k, v in paths.items()
            if k not in ("comtrade", "worldbank")}


def main() -> int:
    """CLI entry point."""
    try:
        transform()
    except Exception as exc:  # noqa: BLE001 - surface any failure to the CLI
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
