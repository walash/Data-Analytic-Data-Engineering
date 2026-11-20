"""Transformation stage of the Inland Waterways (Africa & USA) data pipeline.

Reads every raw CSV in ``data/raw/`` and produces analysis-ready tables in
``data/processed/``:

* ``waterway_infrastructure.csv`` - network length, navigable share and
  waterway density (navigable km per 1,000 sq km of land).
* ``us_freight.csv``              - USACE Mississippi / Ohio River tonnage,
  converted to thousand tons, with region and decade.
* ``africa_freight.csv``          - African river freight & passengers joined
  with infrastructure and population (utilization and per-capita metrics).
* ``container_traffic.csv``       - World Bank container port TEU joined with
  the liner shipping connectivity index.
* ``country_metrics.csv``         - one row per country-year combining GDP,
  population, container traffic, waterway freight and infrastructure.

Cleaning steps: null handling (drop rows without a measured value, fill
descriptive gaps), type casting (year -> int, measures -> float) and
de-duplication on natural keys. Derived metrics:

* freight_per_1000_pop  = freight tons / (population / 1,000)
* utilization_ratio     = freight tons / navigable km (tons per km)
* waterway_density      = navigable km / (land area sq km / 1,000)
"""

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is in requirements.txt
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("transform")

ISO2_TO_ISO3 = {
    "US": "USA", "NG": "NGA", "CD": "COD", "EG": "EGY", "ZA": "ZAF", "GH": "GHA",
    "ET": "ETH", "KE": "KEN", "CM": "CMR", "SN": "SEN", "CI": "CIV", "TZ": "TZA",
    "MZ": "MOZ", "AO": "AGO", "ML": "MLI", "GN": "GIN", "SD": "SDN", "ZM": "ZMB",
}
ISO3_TO_ISO2 = {v: k for k, v in ISO2_TO_ISO3.items()}

# Approximate land area in sq km (World Bank indicator AG.LND.TOTL.K2).
LAND_AREA_SQ_KM = {
    "USA": 9147420, "NGA": 910770, "COD": 2267050, "EGY": 995450,
    "ZAF": 1213090, "GHA": 227540, "ETH": 1128571, "KEN": 569140,
    "CMR": 472710, "SEN": 192530, "CIV": 318000, "TZA": 885800,
    "MOZ": 786380, "AGO": 1246700, "MLI": 1220190, "GIN": 245720,
    "SDN": 1765048, "ZMB": 743390,
}

RAW_FILES = {
    "container": "container_port_traffic.csv",
    "gdp": "gdp_data.csv",
    "freight": "freight_transport.csv",
    "population": "population.csv",
    "infrastructure": "inland_waterway_infrastructure.csv",
    "us_freight": "us_waterway_freight.csv",
    "africa_freight": "africa_waterway_freight.csv",
}


def setup_logging(level_name):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path=CONFIG_PATH):
    """Return the data section of config.yaml (with defaults)."""
    defaults = {"raw_dir": "data/raw", "processed_dir": "data/processed"}
    if yaml is None:
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as handle:
            cfg = yaml.safe_load(handle) or {}
        defaults.update(cfg.get("data", {}))
    except FileNotFoundError:
        logger.warning("Config %s not found; using default paths", path)
    except yaml.YAMLError as exc:
        logger.error("Invalid YAML in %s (%s); using default paths", path, exc)
    return defaults


def resolve_dirs(cfg):
    """Return (raw_dir, processed_dir), honouring the DATA_DIR env var."""
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        base = Path(data_dir).resolve()
        return base / "raw", base / "processed"
    return (PROJECT_ROOT / cfg["raw_dir"]).resolve(), (PROJECT_ROOT / cfg["processed_dir"]).resolve()


def read_raw(raw_dir):
    """Read every expected raw CSV into a dict of DataFrames."""
    frames = {}
    for key, filename in RAW_FILES.items():
        path = raw_dir / filename
        try:
            frames[key] = pd.read_csv(path)
            logger.info("Read %-36s %4d rows x %d cols", filename, *frames[key].shape)
        except FileNotFoundError:
            logger.error("Missing raw file %s - run the ingestion stage first", path)
            raise
        except pd.errors.ParserError as exc:
            logger.error("Could not parse %s: %s", path, exc)
            raise
    return frames


def region_for(iso3):
    return "USA" if iso3 == "USA" else "Africa"


def add_time_and_region(df, iso3_col="iso3"):
    """Add ``region`` and ``decade`` columns (in place) and return df."""
    df["region"] = df[iso3_col].map(region_for)
    df["decade"] = (df["year"] // 10 * 10).astype(int).astype(str) + "s"
    return df


def safe_divide(numerator, denominator):
    """Element-wise division returning NaN where the denominator is 0/NaN."""
    denominator = denominator.replace(0, np.nan)
    return numerator / denominator


def clean_world_bank(df, value_col):
    """Standard cleaning for a World Bank indicator extract."""
    out = df.copy()
    out["iso3"] = out["iso3"].fillna(out["country_code"].map(ISO2_TO_ISO3))
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out[value_col] = pd.to_numeric(out[value_col], errors="coerce").astype(float)
    before = len(out)
    out = out.dropna(subset=["year", value_col])
    out["year"] = out["year"].astype(int)
    out = out.drop_duplicates(subset=["iso3", "year"], keep="first")
    logger.info("Cleaned %s: %d -> %d rows (nulls/duplicates removed)", value_col, before, len(out))
    keep = ["country_code", "country_name", "iso3", "year", value_col]
    return out[keep].sort_values(["iso3", "year"]).reset_index(drop=True)


def transform_infrastructure(df):
    out = df.copy()
    out["country"] = out["country"].fillna("Unknown").str.strip()
    out["major_rivers"] = out["major_rivers"].fillna("Unknown")
    for col in ["total_waterway_km", "navigable_km"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0).astype(float)
    out["year"] = pd.to_numeric(out["year"], errors="coerce").fillna(2023).astype(int)
    out = out.drop_duplicates(subset=["iso3"], keep="last")
    out["iso2"] = out["iso3"].map(ISO3_TO_ISO2)
    out["region"] = out["iso3"].map(region_for)
    out["land_area_sq_km"] = out["iso3"].map(LAND_AREA_SQ_KM).astype(float)
    out["navigable_share"] = safe_divide(out["navigable_km"], out["total_waterway_km"]).round(4)
    out["waterway_density"] = safe_divide(
        out["navigable_km"], out["land_area_sq_km"] / 1000.0
    ).fillna(0.0).round(4)
    cols = ["country", "iso3", "iso2", "region", "total_waterway_km", "navigable_km",
            "navigable_share", "land_area_sq_km", "waterway_density", "major_rivers", "year"]
    return out[cols].sort_values("navigable_km", ascending=False).reset_index(drop=True)


def transform_us_freight(df):
    out = df.copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out["tonnage_million"] = pd.to_numeric(out["tonnage_million"], errors="coerce").astype(float)
    out = out.dropna(subset=["year", "tonnage_million"])
    out["year"] = out["year"].astype(int)
    out["direction"] = out["direction"].fillna("Unknown")
    out = out.drop_duplicates(subset=["year", "waterway", "commodity", "direction"])
    out["country"] = "United States"
    out["iso3"] = "USA"
    out["freight_thousand_tons"] = out["tonnage_million"] * 1000.0
    add_time_and_region(out)
    cols = ["year", "waterway", "commodity", "direction", "tonnage_million",
            "freight_thousand_tons", "country", "iso3", "region", "decade"]
    return out[cols].sort_values(["waterway", "commodity", "year"]).reset_index(drop=True)


def transform_africa_freight(df, infra, population):
    out = df.copy()
    out["year"] = pd.to_numeric(out["year"], errors="coerce")
    out = out.dropna(subset=["year", "iso3"])
    out["year"] = out["year"].astype(int)
    for col in ["freight_thousand_tons", "passengers_thousand", "ports_count"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype(float)
        # Fill gaps with the waterway median so short series stay usable.
        out[col] = out[col].fillna(out.groupby("waterway")[col].transform("median"))
    out = out.dropna(subset=["freight_thousand_tons"])
    out = out.drop_duplicates(subset=["iso3", "waterway", "year"])
    add_time_and_region(out)

    out = out.merge(infra[["iso3", "navigable_km", "waterway_density"]], on="iso3", how="left")
    out = out.merge(population[["iso3", "year", "population"]], on=["iso3", "year"], how="left")
    freight_tons = out["freight_thousand_tons"] * 1000.0
    out["utilization_ratio"] = safe_divide(freight_tons, out["navigable_km"]).round(2)
    out["freight_per_1000_pop"] = safe_divide(freight_tons, out["population"] / 1000.0).round(4)
    out["passengers_per_port"] = safe_divide(out["passengers_thousand"], out["ports_count"]).round(2)
    cols = ["country", "iso3", "waterway", "year", "freight_thousand_tons",
            "passengers_thousand", "ports_count", "region", "decade", "navigable_km",
            "waterway_density", "population", "utilization_ratio",
            "freight_per_1000_pop", "passengers_per_port"]
    return out[cols].sort_values(["country", "waterway", "year"]).reset_index(drop=True)


def transform_container(container, lsci):
    out = container.merge(
        lsci[["iso3", "year", "liner_connectivity_index"]], on=["iso3", "year"], how="left"
    )
    add_time_and_region(out)
    cols = ["country_code", "country_name", "iso3", "year", "container_teu",
            "liner_connectivity_index", "region", "decade"]
    return out[cols].sort_values(["iso3", "year"]).reset_index(drop=True)


def build_country_metrics(gdp, population, container, lsci, infra, us_freight, africa_freight):
    """Combine every source into one country-year fact table."""
    waterway = pd.concat([
        us_freight.groupby(["iso3", "year"], as_index=False)["freight_thousand_tons"].sum(),
        africa_freight.groupby(["iso3", "year"], as_index=False)["freight_thousand_tons"].sum(),
    ], ignore_index=True).rename(columns={"freight_thousand_tons": "waterway_freight_thousand_tons"})

    keys = ["iso3", "year"]
    metrics = gdp[keys + ["gdp_usd"]]
    metrics = metrics.merge(population[keys + ["population"]], on=keys, how="outer")
    metrics = metrics.merge(container[keys + ["container_teu"]], on=keys, how="outer")
    metrics = metrics.merge(lsci[keys + ["liner_connectivity_index"]], on=keys, how="outer")
    metrics = metrics.merge(waterway, on=keys, how="outer")
    metrics = metrics.merge(
        infra[["iso3", "country", "total_waterway_km", "navigable_km", "waterway_density"]],
        on="iso3", how="left",
    )
    metrics = metrics.rename(columns={"country": "country_name"})
    metrics["country_name"] = metrics["country_name"].fillna(metrics["iso3"])
    metrics["iso2"] = metrics["iso3"].map(ISO3_TO_ISO2)
    metrics["year"] = metrics["year"].astype(int)
    add_time_and_region(metrics)

    freight_tons = metrics["waterway_freight_thousand_tons"] * 1000.0
    metrics["gdp_per_capita"] = safe_divide(metrics["gdp_usd"], metrics["population"]).round(2)
    metrics["teu_per_1000_pop"] = safe_divide(metrics["container_teu"], metrics["population"] / 1000.0).round(4)
    metrics["freight_per_1000_pop"] = safe_divide(freight_tons, metrics["population"] / 1000.0).round(4)
    metrics["utilization_ratio"] = safe_divide(freight_tons, metrics["navigable_km"]).round(2)
    metrics = metrics.drop_duplicates(subset=keys)

    cols = ["iso3", "iso2", "country_name", "region", "year", "decade", "gdp_usd",
            "population", "gdp_per_capita", "container_teu", "teu_per_1000_pop",
            "liner_connectivity_index", "waterway_freight_thousand_tons",
            "freight_per_1000_pop", "total_waterway_km", "navigable_km",
            "waterway_density", "utilization_ratio"]
    return metrics[cols].sort_values(keys).reset_index(drop=True)


def transform(raw_dir=None, processed_dir=None):
    """Run the transformation stage; returns dict of output name -> row count."""
    cfg = load_config()
    default_raw, default_processed = resolve_dirs(cfg)
    raw_dir = Path(raw_dir) if raw_dir else default_raw
    processed_dir = Path(processed_dir) if processed_dir else default_processed
    processed_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Transforming %s -> %s", raw_dir, processed_dir)

    raw = read_raw(raw_dir)
    gdp = clean_world_bank(raw["gdp"], "gdp_usd")
    population = clean_world_bank(raw["population"], "population")
    container = clean_world_bank(raw["container"], "container_teu")
    lsci = clean_world_bank(
        raw["freight"].rename(columns={"freight_million_ton_km": "liner_connectivity_index"}),
        "liner_connectivity_index",
    )

    infra = transform_infrastructure(raw["infrastructure"])
    us_freight = transform_us_freight(raw["us_freight"])
    africa_freight = transform_africa_freight(raw["africa_freight"], infra, population)
    container_traffic = transform_container(container, lsci)
    metrics = build_country_metrics(gdp, population, container, lsci, infra, us_freight, africa_freight)

    outputs = {
        "waterway_infrastructure.csv": infra,
        "us_freight.csv": us_freight,
        "africa_freight.csv": africa_freight,
        "container_traffic.csv": container_traffic,
        "country_metrics.csv": metrics,
    }
    summary = {}
    for filename, frame in outputs.items():
        target = processed_dir / filename
        try:
            frame.to_csv(target, index=False)
        except OSError as exc:
            logger.error("Could not write %s: %s", target, exc)
            raise
        summary[filename] = len(frame)
        logger.info("Wrote %-30s %4d rows", filename, len(frame))
    logger.info("Transformation complete: %s", summary)
    return summary


def main():
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    try:
        transform()
    except Exception as exc:
        logger.exception("Transformation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
