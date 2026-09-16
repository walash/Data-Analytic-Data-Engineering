"""Transformation module for the AI Adoption & Digital Infrastructure pipeline.

Reads the five raw indicator CSVs produced by :mod:`src.ingestion.ingest`,
merges them into a single wide-format table (one row per country-year), cleans
and type-casts the data, engineers derived analytical columns, classifies each
country into an African sub-region (or USA), and writes two processed outputs:

* ``data/processed/digital_infrastructure.csv`` - full country-year panel with
  all indicators plus derived columns.
* ``data/processed/country_summary.csv`` - the latest available year per
  country with region labels, used for regional aggregation and dashboards.

Derived columns
---------------
* ``internet_mobile_ratio`` - internet penetration divided by mobile
  subscriptions per 100 (a measure of how "beyond-mobile" connectivity is).
* ``digital_readiness_score`` - a 0-100 composite built from min-max
  normalised internet, broadband and mobile metrics.

Run directly (``python src/transformation/transform.py``) or import
:func:`transform` from an orchestrator.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import yaml

LOGGER = logging.getLogger("transformation")

# ISO-3 code -> African sub-region. USA is handled separately.
REGION_MAP: Dict[str, str] = {
    # East Africa
    "KEN": "East Africa",
    "ETH": "East Africa",
    "TZA": "East Africa",
    "UGA": "East Africa",
    "RWA": "East Africa",
    "MOZ": "East Africa",
    "MDG": "East Africa",
    "ZMB": "East Africa",
    "ZWE": "East Africa",
    "MWI": "East Africa",
    "ERI": "East Africa",
    "DJI": "East Africa",
    "SOM": "East Africa",
    "COM": "East Africa",
    "MUS": "East Africa",
    "SSD": "East Africa",
    # West Africa
    "NGA": "West Africa",
    "GHA": "West Africa",
    "SEN": "West Africa",
    "CIV": "West Africa",
    "CMR": "West Africa",
    "BEN": "West Africa",
    "TGO": "West Africa",
    "MLI": "West Africa",
    "BFA": "West Africa",
    "NER": "West Africa",
    "GIN": "West Africa",
    "STP": "West Africa",
    "CPV": "West Africa",
    "GMB": "West Africa",
    "SLE": "West Africa",
    "LBR": "West Africa",
    # North Africa
    "EGY": "North Africa",
    "MAR": "North Africa",
    "TUN": "North Africa",
    "DZA": "North Africa",
    "LBY": "North Africa",
    "SDN": "North Africa",
    # Southern Africa
    "ZAF": "Southern Africa",
    "AGO": "Southern Africa",
    "NAM": "Southern Africa",
    "BWA": "Southern Africa",
    # USA
    "USA": "USA",
}


def _configure_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format.

    Parameters
    ----------
    level:
        Logging level name (e.g. ``"INFO"``).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return Path(__file__).resolve().parents[2]


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and parse the YAML configuration file.

    Parameters
    ----------
    config_path:
        Optional explicit path to ``config.yaml``.

    Returns
    -------
    dict
        Parsed configuration mapping.

    Raises
    ------
    FileNotFoundError
        If the configuration file cannot be located.
    """
    if config_path is None:
        config_path = os.environ.get(
            "CONFIG_PATH", str(project_root() / "config" / "config.yaml")
        )
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _read_indicator(raw_dir: Path, file_stem: str, value_col: str) -> pd.DataFrame:
    """Read one raw indicator CSV into a tidy, typed DataFrame.

    Parameters
    ----------
    raw_dir:
        Directory containing the raw CSV files.
    file_stem:
        File name without extension (e.g. ``"internet_users"``).
    value_col:
        Target column name for the indicator value in the merged frame.

    Returns
    -------
    pandas.DataFrame
        Columns ``country_code, country_name, year, <value_col>``.

    Raises
    ------
    FileNotFoundError
        If the expected CSV does not exist.
    """
    path = raw_dir / f"{file_stem}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Expected raw file missing: {path}")
    frame = pd.read_csv(path)
    frame = frame[["country_code", "country_name", "year", "value"]].copy()
    frame["year"] = pd.to_numeric(frame["year"], errors="coerce").astype("Int64")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["country_code", "year"])
    frame = frame.rename(columns={"value": value_col})
    # Deduplicate on the natural key, keeping the last (most complete) record.
    frame = frame.drop_duplicates(subset=["country_code", "year"], keep="last")
    LOGGER.info("Read %s: %d rows", path.name, len(frame))
    return frame


def merge_indicators(raw_dir: Path, indicators: Dict[str, Any]) -> pd.DataFrame:
    """Merge all indicator CSVs into a wide country-year panel.

    Parameters
    ----------
    raw_dir:
        Directory containing the raw CSV files.
    indicators:
        Indicator configuration mapping (``id -> {file, column, name}``).

    Returns
    -------
    pandas.DataFrame
        Wide-format panel keyed by ``country_code`` and ``year``.
    """
    merged: Optional[pd.DataFrame] = None
    name_lookup: Optional[pd.DataFrame] = None
    for meta in indicators.values():
        frame = _read_indicator(raw_dir, meta["file"], meta["column"])
        names = frame[["country_code", "country_name"]].drop_duplicates()
        name_lookup = (
            names
            if name_lookup is None
            else pd.concat([name_lookup, names]).drop_duplicates("country_code")
        )
        keyed = frame.drop(columns=["country_name"])
        merged = (
            keyed
            if merged is None
            else merged.merge(keyed, on=["country_code", "year"], how="outer")
        )

    assert merged is not None and name_lookup is not None
    merged = merged.merge(name_lookup, on="country_code", how="left")
    merged["year"] = merged["year"].astype("Int64")
    merged = merged.sort_values(["country_code", "year"]).reset_index(drop=True)
    LOGGER.info(
        "Merged panel: %d country-year rows across %d countries",
        len(merged),
        merged["country_code"].nunique(),
    )
    return merged


def _minmax(series: pd.Series) -> pd.Series:
    """Min-max normalise a numeric series to the 0-100 range.

    Parameters
    ----------
    series:
        Numeric series (may contain NaN).

    Returns
    -------
    pandas.Series
        Values scaled to ``[0, 100]``; a constant series maps to 0.
    """
    low, high = series.min(), series.max()
    if pd.isna(low) or pd.isna(high) or high == low:
        return series.apply(lambda _: 0.0)
    return (series - low) / (high - low) * 100.0


def add_derived_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Add engineered analytical columns to the merged panel.

    Adds ``internet_mobile_ratio``, three normalised component columns and the
    composite ``digital_readiness_score``.

    Parameters
    ----------
    frame:
        Wide-format panel from :func:`merge_indicators`.

    Returns
    -------
    pandas.DataFrame
        Panel with the additional derived columns.
    """
    out = frame.copy()

    mobile = out["mobile_subscriptions_per100"].replace(0, pd.NA)
    out["internet_mobile_ratio"] = (out["internet_users_pct"] / mobile).astype(float)

    out["norm_internet"] = _minmax(out["internet_users_pct"])
    out["norm_broadband"] = _minmax(out["fixed_broadband_per100"])
    out["norm_mobile"] = _minmax(out["mobile_subscriptions_per100"])
    out["digital_readiness_score"] = out[
        ["norm_internet", "norm_broadband", "norm_mobile"]
    ].mean(axis=1, skipna=True).round(2)

    out["internet_mobile_ratio"] = out["internet_mobile_ratio"].round(4)
    LOGGER.info("Added derived columns (readiness score, internet/mobile ratio)")
    return out


def tag_region(frame: pd.DataFrame) -> pd.DataFrame:
    """Attach a ``region`` label to each row based on country code.

    Parameters
    ----------
    frame:
        Panel containing a ``country_code`` column.

    Returns
    -------
    pandas.DataFrame
        Panel with an added ``region`` column ("Unknown" if unmapped).
    """
    out = frame.copy()
    out["region"] = out["country_code"].map(REGION_MAP).fillna("Unknown")
    return out


def build_country_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Build a latest-year-per-country summary table.

    For each country the most recent year that has a non-null internet
    penetration value is selected; if none exists the latest year overall is
    used.

    Parameters
    ----------
    frame:
        Full panel with region labels and derived columns.

    Returns
    -------
    pandas.DataFrame
        One row per country with headline metrics for its latest year.
    """
    rows = []
    for code, group in frame.groupby("country_code"):
        group = group.sort_values("year")
        with_internet = group.dropna(subset=["internet_users_pct"])
        latest = (with_internet if not with_internet.empty else group).iloc[-1]
        rows.append(latest)
    summary = pd.DataFrame(rows).reset_index(drop=True)
    columns = [
        "country_code",
        "country_name",
        "region",
        "year",
        "internet_users_pct",
        "mobile_subscriptions_per100",
        "fixed_broadband_per100",
        "hightech_exports_pct",
        "rnd_expenditure_pct",
        "internet_mobile_ratio",
        "digital_readiness_score",
    ]
    summary = summary[columns].sort_values(
        "digital_readiness_score", ascending=False
    ).reset_index(drop=True)
    LOGGER.info("Built country summary: %d countries", len(summary))
    return summary


def transform(config_path: Optional[str] = None) -> Dict[str, int]:
    """Run the full transformation stage and write processed CSVs.

    Parameters
    ----------
    config_path:
        Optional path to the configuration file.

    Returns
    -------
    dict
        Mapping of output name to row count.
    """
    config = load_config(config_path)
    _configure_logging(config.get("pipeline", {}).get("log_level", "INFO"))

    raw_dir = project_root() / config["paths"]["raw_data"]
    processed_dir = project_root() / config["paths"]["processed_data"]
    processed_dir.mkdir(parents=True, exist_ok=True)

    merged = merge_indicators(raw_dir, config["indicators"])
    enriched = add_derived_columns(merged)
    enriched = tag_region(enriched)

    ordered_cols = [
        "country_code",
        "country_name",
        "region",
        "year",
        "internet_users_pct",
        "mobile_subscriptions_per100",
        "fixed_broadband_per100",
        "hightech_exports_pct",
        "rnd_expenditure_pct",
        "internet_mobile_ratio",
        "norm_internet",
        "norm_broadband",
        "norm_mobile",
        "digital_readiness_score",
    ]
    enriched = enriched[ordered_cols]

    panel_path = processed_dir / "digital_infrastructure.csv"
    enriched.to_csv(panel_path, index=False)
    LOGGER.info("Wrote %d rows to %s", len(enriched), panel_path)

    summary = build_country_summary(enriched)
    summary_path = processed_dir / "country_summary.csv"
    summary.to_csv(summary_path, index=False)
    LOGGER.info("Wrote %d rows to %s", len(summary), summary_path)

    return {
        "digital_infrastructure": len(enriched),
        "country_summary": len(summary),
    }


def main() -> None:
    """CLI / Airflow entry point that runs :func:`transform`."""
    transform()


if __name__ == "__main__":
    main()
