"""Transformation stage for the Africa-USA Road Infrastructure & Trade Corridors pipeline.

Reads the tidy indicator CSVs produced by ``src/ingestion/ingest.py`` from
``data/raw/``, cleans and merges them into one country-year master table,
and derives benchmark metrics against the USA.

Outputs:
    data/processed/master_transport_trade.csv  - one row per country-year
    data/processed/country_summary.csv         - one row per country (latest values)

Cleaning rules:
    * ``year`` cast to int, ``value`` cast to float; unparsable rows dropped.
    * Missing ISO3 codes (legacy road indicators) recovered from country names.
    * Exact duplicates and duplicate (country, indicator, year) keys removed.
    * Indicator values forward-filled within each country over time.
    * Country-years with more than 50% of indicators still missing are dropped.
"""

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = Path(os.getenv("RAW_DATA_DIR", PROJECT_ROOT / "data" / "raw"))
PROCESSED_DIR = Path(os.getenv("PROCESSED_DATA_DIR", PROJECT_ROOT / "data" / "processed"))

INDICATOR_FILES = {
    "lpi_overall": "lpi_overall.csv",
    "lpi_infrastructure": "lpi_infrastructure.csv",
    "road_density": "road_density.csv",
    "paved_roads_pct": "paved_roads_pct.csv",
    "merchandise_exports": "merchandise_exports.csv",
    "trade_pct_gdp": "trade_pct_gdp.csv",
    "gdp_per_capita": "gdp_per_capita.csv",
}

NAME_TO_ISO3 = {
    "Nigeria": "NGA", "Ghana": "GHA", "Kenya": "KEN", "Ethiopia": "ETH",
    "South Africa": "ZAF", "Egypt, Arab Rep.": "EGY", "Morocco": "MAR",
    "Tanzania": "TZA", "Uganda": "UGA", "Senegal": "SEN", "Cameroon": "CMR",
    "Cote d'Ivoire": "CIV", "Zambia": "ZMB", "Mozambique": "MOZ",
    "Rwanda": "RWA", "United States": "USA",
}
ISO2_TO_ISO3 = {
    "NG": "NGA", "GH": "GHA", "KE": "KEN", "ET": "ETH", "ZA": "ZAF",
    "EG": "EGY", "MA": "MAR", "TZ": "TZA", "UG": "UGA", "SN": "SEN",
    "CM": "CMR", "CI": "CIV", "ZM": "ZMB", "MZ": "MOZ", "RW": "RWA", "US": "USA",
}
BENCHMARK = "USA"
MAX_MISSING_RATIO = 0.5
OPENNESS_BINS = [-np.inf, 40, 70, np.inf]
OPENNESS_LABELS = ["Low", "Moderate", "High"]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("transform")


def read_indicator(path, key):
    """Read and clean a single tidy indicator CSV.

    Args:
        path: CSV path with columns country_code, country_name, indicator, year, value.
        key: Short indicator key used as the column name in the master table.

    Returns:
        DataFrame with columns country_code, country_name, indicator, year, value.
    """
    df = pd.read_csv(path, dtype={"country_code": str})
    df["indicator"] = key
    df["country_code"] = df["country_code"].fillna("").str.strip().str.upper()
    df["country_code"] = df["country_code"].replace(ISO2_TO_ISO3)
    missing = df["country_code"] == ""
    df.loc[missing, "country_code"] = df.loc[missing, "country_name"].map(NAME_TO_ISO3)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["country_code", "year", "value"])
    df["year"] = df["year"].astype(int)
    df["value"] = df["value"].astype(float)
    logger.info("%-20s %3d rows read, %3d kept", key, before, len(df))
    return df[["country_code", "country_name", "indicator", "year", "value"]]


def load_raw(raw_dir=RAW_DIR):
    """Load every configured indicator CSV into one long, deduplicated DataFrame.

    Args:
        raw_dir: Directory holding the raw indicator CSVs.

    Returns:
        Long-format DataFrame (one row per country, indicator, year).

    Raises:
        FileNotFoundError: If no indicator CSVs are found.
    """
    frames = []
    for key, fname in INDICATOR_FILES.items():
        path = Path(raw_dir) / fname
        if not path.exists():
            logger.warning("Missing raw file %s - skipping", path)
            continue
        frames.append(read_indicator(path, key))
    if not frames:
        raise FileNotFoundError(f"No indicator CSVs found in {raw_dir}")
    long_df = pd.concat(frames, ignore_index=True).drop_duplicates()
    before = len(long_df)
    long_df = long_df.drop_duplicates(subset=["country_code", "indicator", "year"], keep="first")
    logger.info("Deduplication removed %d key-duplicate rows", before - len(long_df))
    return long_df


def build_master(long_df):
    """Pivot to country-year and forward-fill each indicator within country.

    Args:
        long_df: Output of :func:`load_raw`.

    Returns:
        Wide DataFrame with one column per indicator plus ``lpi_observed``
        (1 when the LPI value comes from an actual survey year, 0 if filled).
    """
    names = (long_df.drop_duplicates("country_code")
             .set_index("country_code")["country_name"].to_dict())
    wide = long_df.pivot_table(index=["country_code", "year"], columns="indicator",
                               values="value", aggfunc="first").reset_index()
    wide.columns.name = None
    for key in INDICATOR_FILES:
        if key not in wide.columns:
            wide[key] = np.nan
    indicator_cols = list(INDICATOR_FILES)
    wide = wide.sort_values(["country_code", "year"]).reset_index(drop=True)
    wide["lpi_observed"] = wide["lpi_overall"].notna().astype(int)
    wide[indicator_cols] = wide.groupby("country_code")[indicator_cols].ffill()
    wide["country_name"] = wide["country_code"].map(names)
    wide["region"] = np.where(wide["country_code"] == BENCHMARK, "North America", "Africa")
    return wide


def add_derived_metrics(master):
    """Add USA-benchmark LPI gaps and the trade openness category.

    Args:
        master: Output of :func:`build_master`.

    Returns:
        DataFrame with ``lpi_gap_vs_usa``, ``lpi_infra_gap_vs_usa``,
        ``lpi_pct_of_usa`` and ``trade_openness_category`` columns.
    """
    usa = (master[master["country_code"] == BENCHMARK]
           .set_index("year")[["lpi_overall", "lpi_infrastructure"]]
           .rename(columns={"lpi_overall": "usa_lpi", "lpi_infrastructure": "usa_lpi_infra"}))
    out = master.merge(usa, left_on="year", right_index=True, how="left")
    out["lpi_gap_vs_usa"] = (out["lpi_overall"] - out["usa_lpi"]).round(4)
    out["lpi_infra_gap_vs_usa"] = (out["lpi_infrastructure"] - out["usa_lpi_infra"]).round(4)
    out["lpi_pct_of_usa"] = (out["lpi_overall"] / out["usa_lpi"] * 100).round(2)
    out["trade_openness_category"] = pd.cut(out["trade_pct_gdp"], bins=OPENNESS_BINS,
                                            labels=OPENNESS_LABELS).astype(str)
    out.loc[out["trade_pct_gdp"].isna(), "trade_openness_category"] = "Unknown"
    cols = ["country_code", "country_name", "region", "year", *INDICATOR_FILES,
            "lpi_observed", "lpi_gap_vs_usa", "lpi_infra_gap_vs_usa", "lpi_pct_of_usa",
            "trade_openness_category"]
    return out[cols].sort_values(["country_code", "year"]).reset_index(drop=True)


def drop_sparse_rows(master, max_missing_ratio=MAX_MISSING_RATIO):
    """Drop country-years where more than ``max_missing_ratio`` of indicators are null.

    Applied after derived metrics so USA benchmark values from sparse years
    are still available when computing the LPI gaps.

    Args:
        master: Wide country-year DataFrame.
        max_missing_ratio: Maximum tolerated share of missing indicators.

    Returns:
        Filtered DataFrame.
    """
    missing_ratio = master[list(INDICATOR_FILES)].isna().mean(axis=1)
    dropped = int((missing_ratio > max_missing_ratio).sum())
    logger.info("Dropped %d country-years with >%.0f%% indicators missing",
                dropped, max_missing_ratio * 100)
    return master[missing_ratio <= max_missing_ratio].reset_index(drop=True)


def build_country_summary(long_df, master):
    """Summarise each country with the latest observed value of every indicator.

    Uses the unfilled long data so each value carries its true observation year.

    Args:
        long_df: Output of :func:`load_raw`.
        master: Output of :func:`add_derived_metrics`.

    Returns:
        One-row-per-country DataFrame including LPI rank among all 16 countries.
    """
    latest = (long_df.sort_values("year")
              .groupby(["country_code", "indicator"]).tail(1))
    values = latest.pivot(index="country_code", columns="indicator", values="value")
    years = latest.pivot(index="country_code", columns="indicator", values="year")
    years.columns = [f"{c}_year" for c in years.columns]
    summary = values.join(years).reset_index()
    names = long_df.drop_duplicates("country_code").set_index("country_code")["country_name"]
    summary.insert(1, "country_name", summary["country_code"].map(names))
    summary.insert(2, "region", np.where(summary["country_code"] == BENCHMARK, "North America", "Africa"))
    usa_row = summary[summary["country_code"] == BENCHMARK]
    if not usa_row.empty:
        usa_lpi = usa_row["lpi_overall"].iloc[0]
        usa_infra = usa_row["lpi_infrastructure"].iloc[0]
        summary["lpi_gap_vs_usa"] = (summary["lpi_overall"] - usa_lpi).round(4)
        summary["lpi_infra_gap_vs_usa"] = (summary["lpi_infrastructure"] - usa_infra).round(4)
    else:
        summary["lpi_gap_vs_usa"] = np.nan
        summary["lpi_infra_gap_vs_usa"] = np.nan
    summary["trade_openness_category"] = pd.cut(summary["trade_pct_gdp"], bins=OPENNESS_BINS,
                                                labels=OPENNESS_LABELS).astype(str)
    summary.loc[summary["trade_pct_gdp"].isna(), "trade_openness_category"] = "Unknown"
    summary["lpi_rank"] = summary["lpi_overall"].rank(ascending=False, method="min").astype("Int64")
    observed_years = master.groupby("country_code")["year"].agg(["min", "max"])
    summary["first_year"] = summary["country_code"].map(observed_years["min"]).astype("Int64")
    summary["last_year"] = summary["country_code"].map(observed_years["max"]).astype("Int64")
    for col in INDICATOR_FILES:
        if col not in summary.columns:
            summary[col] = np.nan
    return summary.sort_values("lpi_rank").reset_index(drop=True)


def transform(raw_dir=RAW_DIR, processed_dir=PROCESSED_DIR):
    """Run the full transformation and write processed CSVs.

    Args:
        raw_dir: Input directory with raw CSVs.
        processed_dir: Output directory for processed CSVs.

    Returns:
        Tuple (master DataFrame, country summary DataFrame).
    """
    logger.info("Starting transformation from %s", raw_dir)
    long_df = load_raw(raw_dir)
    master = drop_sparse_rows(add_derived_metrics(build_master(long_df)))
    summary = build_country_summary(long_df, master)
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    master_path = processed_dir / "master_transport_trade.csv"
    summary_path = processed_dir / "country_summary.csv"
    master.to_csv(master_path, index=False)
    summary.to_csv(summary_path, index=False)
    logger.info("Wrote %s (%d rows, %d cols)", master_path, *master.shape)
    logger.info("Wrote %s (%d rows, %d cols)", summary_path, *summary.shape)
    return master, summary


def main():
    """CLI entry point. Returns a process exit code."""
    try:
        transform()
    except (FileNotFoundError, ValueError, KeyError) as exc:
        logger.error("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
