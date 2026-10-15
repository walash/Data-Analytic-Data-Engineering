"""Transformation module for the AI in Agriculture project.

Reads the raw World Bank JSON payloads from ``data/raw/``, reshapes them into
tidy wide-format tables, engineers AI/analytics features and writes the
analysis-ready CSVs into ``data/processed/``.

Outputs:
    * africa_agriculture.csv          -> merged wide table for 15 African countries
    * usa_agriculture.csv             -> merged wide table for the USA
    * combined_agriculture.csv        -> Africa + USA concatenated
    * agriculture_with_features.csv   -> combined table + engineered AI features

Run:
    python src/transformation/transform.py
"""

import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("transformation")

# Logical indicator -> output column name (wide format).
COLUMN_MAP = {
    "cereal_yield": "cereal_yield_kg_ha",
    "agricultural_land": "agr_land_pct",
    "food_production_index": "food_production_index",
    "fertilizer_consumption": "fertilizer_kg_ha",
    "agr_value_added": "agr_value_added_pct",
    "rural_population": "rural_population_pct",
}

AFRICA_FILES = {
    "cereal_yield": "wb_cereal_yield.json",
    "agricultural_land": "wb_agricultural_land.json",
    "food_production_index": "wb_food_production_index.json",
    "fertilizer_consumption": "wb_fertilizer_consumption.json",
    "agr_value_added": "wb_agr_value_added.json",
    "rural_population": "wb_rural_population.json",
}

USA_FILES = {
    "cereal_yield": "wb_usa_cereal_yield.json",
    "agricultural_land": "wb_usa_agr_land.json",
    "food_production_index": "wb_usa_food_production.json",
    "fertilizer_consumption": "wb_usa_fertilizer.json",
    "agr_value_added": "wb_usa_agr_value_added.json",
    "rural_population": "wb_usa_rural_pop.json",
}

VALUE_COLUMNS = list(COLUMN_MAP.values())


def load_wb_json(filepath):
    """Load a World Bank JSON file into a tidy long DataFrame.

    Args:
        filepath: Path to the raw World Bank JSON payload.

    Returns:
        DataFrame with columns:
        ``country_code``, ``country_name``, ``year``, ``value``, ``indicator``.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.warning("File not found, returning empty frame: %s", filepath)
        return pd.DataFrame(
            columns=["country_code", "country_name", "year", "value", "indicator"]
        )

    with open(filepath, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        logger.warning("Empty or malformed payload: %s", filepath)
        return pd.DataFrame(
            columns=["country_code", "country_name", "year", "value", "indicator"]
        )

    records = []
    for item in payload[1]:
        country = item.get("country") or {}
        indicator = item.get("indicator") or {}
        records.append(
            {
                "country_code": item.get("countryiso3code"),
                "country_name": country.get("value"),
                "year": item.get("date"),
                "value": item.get("value"),
                "indicator": indicator.get("id"),
            }
        )

    df = pd.DataFrame.from_records(records)
    if not df.empty:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
    logger.info("Loaded %d rows from %s", len(df), filepath.name)
    return df


def _merge_indicator_files(raw_dir, file_map):
    """Merge the 6 indicator files into a single wide DataFrame."""
    raw_dir = Path(raw_dir)
    merged = None

    for name, filename in file_map.items():
        long_df = load_wb_json(raw_dir / filename)
        if long_df.empty:
            continue
        wide = long_df.rename(columns={"value": COLUMN_MAP[name]})[
            ["country_code", "country_name", "year", COLUMN_MAP[name]]
        ]
        if merged is None:
            merged = wide
        else:
            merged = merged.merge(
                wide, on=["country_code", "country_name", "year"], how="outer"
            )

    if merged is None:
        merged = pd.DataFrame(
            columns=["country_code", "country_name", "year"] + VALUE_COLUMNS
        )

    # Ensure all value columns exist even if a file was missing.
    for col in VALUE_COLUMNS:
        if col not in merged.columns:
            merged[col] = np.nan

    return merged


def _clean_wide(df, region):
    """Drop all-null rows, cast types and tag the region."""
    if df.empty:
        df["region"] = region
        return df

    # Drop rows where every measured indicator is null.
    df = df.dropna(subset=VALUE_COLUMNS, how="all").copy()

    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    for col in VALUE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["country_code"] = df["country_code"].astype("string")
    df["country_name"] = df["country_name"].astype("string")
    df["region"] = region

    df = df.sort_values(["country_name", "year"]).reset_index(drop=True)
    return df


def transform_africa_data(raw_dir, processed_dir):
    """Build the merged Africa wide table and save it as CSV."""
    logger.info("=== Transforming Africa data ===")
    merged = _merge_indicator_files(raw_dir, AFRICA_FILES)
    cleaned = _clean_wide(merged, region="Africa")

    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "africa_agriculture.csv"
    cleaned.to_csv(out_path, index=False)
    logger.info("Saved %d Africa rows -> %s", len(cleaned), out_path)
    return cleaned


def transform_usa_data(raw_dir, processed_dir):
    """Build the merged USA wide table and save it as CSV."""
    logger.info("=== Transforming USA data ===")
    merged = _merge_indicator_files(raw_dir, USA_FILES)
    cleaned = _clean_wide(merged, region="USA")

    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    out_path = processed_dir / "usa_agriculture.csv"
    cleaned.to_csv(out_path, index=False)
    logger.info("Saved %d USA rows -> %s", len(cleaned), out_path)
    return cleaned


def combine_datasets(processed_dir):
    """Concatenate the Africa and USA tables into one combined CSV."""
    logger.info("=== Combining Africa + USA data ===")
    processed_dir = Path(processed_dir)
    frames = []
    for fname in ("africa_agriculture.csv", "usa_agriculture.csv"):
        fpath = processed_dir / fname
        if fpath.exists():
            frames.append(pd.read_csv(fpath))
        else:
            logger.warning("Missing processed file: %s", fpath)

    if frames:
        combined = pd.concat(frames, ignore_index=True)
    else:
        combined = pd.DataFrame(
            columns=["country_code", "country_name", "year"]
            + VALUE_COLUMNS
            + ["region"]
        )

    combined = combined.sort_values(["region", "country_name", "year"]).reset_index(
        drop=True
    )
    out_path = processed_dir / "combined_agriculture.csv"
    combined.to_csv(out_path, index=False)
    logger.info("Saved %d combined rows -> %s", len(combined), out_path)
    return combined


def _normalize(series):
    """Min-max normalize a numeric series to 0-100 (NaN-safe)."""
    s = pd.to_numeric(series, errors="coerce")
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return pd.Series([50.0] * len(s), index=s.index)
    return (s - lo) / (hi - lo) * 100.0


def add_ai_features(processed_dir):
    """Engineer AI/analytics features on top of the combined dataset.

    Features added:
        * yield_growth_rate       -> YoY % change of cereal yield per country
        * fertilizer_efficiency   -> cereal yield per unit of fertilizer
        * food_security_score     -> normalized 0-100 composite indicator
        * ai_investment_priority  -> quartile bucket (Critical/High/Medium/Low)
    """
    logger.info("=== Engineering AI features ===")
    processed_dir = Path(processed_dir)
    combined_path = processed_dir / "combined_agriculture.csv"
    if not combined_path.exists():
        logger.error("combined_agriculture.csv not found; run combine_datasets first")
        return pd.DataFrame()

    df = pd.read_csv(combined_path)
    df = df.sort_values(["country_name", "year"]).reset_index(drop=True)

    # Year-over-year cereal yield growth rate (%) per country.
    df["yield_growth_rate"] = (
        df.groupby("country_name")["cereal_yield_kg_ha"].pct_change(fill_method=None)
        * 100.0
    )

    # Fertilizer efficiency: yield produced per kg of fertilizer applied.
    df["fertilizer_efficiency"] = np.where(
        (df["fertilizer_kg_ha"].notna()) & (df["fertilizer_kg_ha"] > 0),
        df["cereal_yield_kg_ha"] / df["fertilizer_kg_ha"],
        np.nan,
    )

    # Food security score: normalized composite of positive drivers minus risk.
    yield_n = _normalize(df["cereal_yield_kg_ha"])
    food_n = _normalize(df["food_production_index"])
    fert_n = _normalize(df["fertilizer_kg_ha"])
    # Lower agriculture-value-added share of GDP tends to indicate a more
    # diversified, food-secure economy; invert it as a mild positive driver.
    diversification_n = 100.0 - _normalize(df["agr_value_added_pct"])

    df["food_security_score"] = (
        0.40 * yield_n
        + 0.30 * food_n
        + 0.20 * fert_n
        + 0.10 * diversification_n
    ).round(2)

    # AI investment priority: countries with the LOWEST food security score
    # are the most critical targets for AI-driven agricultural investment.
    country_scores = (
        df.groupby("country_name")["food_security_score"].mean().dropna()
    )
    priority_labels = pd.Series(index=country_scores.index, dtype="object")
    if len(country_scores) >= 4 and country_scores.nunique() >= 4:
        buckets = pd.qcut(
            country_scores,
            q=4,
            labels=["Critical", "High", "Medium", "Low"],
        )
        priority_labels = buckets.astype("object")
    else:
        # Fallback for very small cohorts: rank-based thresholds.
        ranked = country_scores.rank(pct=True)
        for country, pct in ranked.items():
            if pct <= 0.25:
                priority_labels[country] = "Critical"
            elif pct <= 0.50:
                priority_labels[country] = "High"
            elif pct <= 0.75:
                priority_labels[country] = "Medium"
            else:
                priority_labels[country] = "Low"

    df["ai_investment_priority"] = (
        df["country_name"].map(priority_labels).fillna("Medium")
    )

    # Round engineered numeric columns for readability.
    df["yield_growth_rate"] = df["yield_growth_rate"].round(2)
    df["fertilizer_efficiency"] = df["fertilizer_efficiency"].round(2)

    out_path = processed_dir / "agriculture_with_features.csv"
    df.to_csv(out_path, index=False)
    logger.info(
        "Saved %d feature rows (%d columns) -> %s",
        len(df),
        df.shape[1],
        out_path,
    )
    return df


def main():
    """Run the full transformation pipeline end to end."""
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"

    logger.info("Raw dir: %s | Processed dir: %s", raw_dir, processed_dir)

    transform_africa_data(raw_dir, processed_dir)
    transform_usa_data(raw_dir, processed_dir)
    combine_datasets(processed_dir)
    add_ai_features(processed_dir)

    logger.info("Transformation pipeline complete.")


if __name__ == "__main__":
    main()
