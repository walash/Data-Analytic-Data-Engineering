"""Ingestion stage for the Deepwater Offshore Oil Production project.

Downloads two public datasets and writes them to ``data/raw/``:

1. Our World in Data (OWID) energy dataset
   https://raw.githubusercontent.com/owid/energy-data/master/owid-energy-data.csv
   -> filtered to the deepwater producer set and 2010-2023, saved as
   ``owid_oil_production.csv``.
2. World Bank Indicators API, oil rents (% of GDP, NY.GDP.PETR.RT.ZS)
   https://api.worldbank.org/v2/country/{iso}/indicator/NY.GDP.PETR.RT.ZS?format=json
   -> raw JSON saved as ``wb_oil_rents_raw.json``.

The two sources are then merged on ISO3 code + year into
``oil_production_merged.csv`` (the input of the transformation stage).

Usage (from the project root)::

    python src/ingestion/ingest.py            # download + merge
    python src/ingestion/ingest.py --dry-run  # validate config only

Network calls use ``tenacity`` exponential-backoff retries; any failure is
logged and re-raised so orchestrators (Airflow, Docker) mark the run failed.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yaml
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

OWID_COLUMNS = {
    "country": "country",
    "iso_code": "iso_code",
    "year": "year",
    "oil_production": "oil_production_twh",
    "oil_consumption": "oil_consumption_twh",
    "oil_prod_per_capita": "oil_prod_per_capita",
    "oil_cons_change_pct": "oil_cons_change_pct",
    "fossil_fuel_consumption": "fossil_fuel_consumption",
    "gdp": "gdp",
    "population": "population",
}

MERGED_COLUMNS = [
    "country", "iso_code", "year", "oil_production_twh",
    "oil_consumption_twh", "oil_prod_per_capita",
    "fossil_fuel_consumption", "gdp", "population", "oil_rents_pct_gdp",
]

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | ingest | %(message)s",
)
logger = logging.getLogger("ingest")


class IngestionError(RuntimeError):
    """Raised when a source cannot be downloaded or validated."""


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load the YAML configuration (honours the CONFIG_PATH env variable)."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def raw_dir(config: dict[str, Any]) -> Path:
    """Return (and create) the raw data directory."""
    path = Path(os.getenv("DATA_RAW_DIR", config["paths"]["raw"]))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


@retry(
    retry=retry_if_exception_type((requests.ConnectionError,
                                   requests.Timeout,
                                   requests.HTTPError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def http_get(url: str, timeout: int = 120) -> requests.Response:
    """GET a URL with retries on connection errors, timeouts and 5xx/4xx."""
    logger.info("GET %s", url)
    response = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": "deepwater-oil-pipeline/1.0"},
    )
    response.raise_for_status()
    return response


def fetch_owid(config: dict[str, Any], out_dir: Path) -> pd.DataFrame:
    """Download the OWID energy CSV and keep oil columns for target countries."""
    url = config["data_sources"]["owid_energy_csv"]
    timeout = config["pipeline"]["request_timeout"]
    isos = list(config["countries"].keys())
    start, end = config["years"]["start"], config["years"]["end"]

    response = http_get(url, timeout=timeout)
    full = pd.read_csv(io.StringIO(response.text), low_memory=False)
    missing = [c for c in OWID_COLUMNS if c not in full.columns]
    if missing:
        raise IngestionError(f"OWID schema changed, missing columns: {missing}")

    subset = full.loc[
        full["iso_code"].isin(isos) & full["year"].between(start, end),
        list(OWID_COLUMNS),
    ].rename(columns=OWID_COLUMNS)
    subset = subset.sort_values(["country", "year"]).reset_index(drop=True)
    if subset.empty:
        raise IngestionError("OWID download returned no rows for target countries")

    out_path = out_dir / config["paths"]["raw_files"]["owid"]
    owid_cols = ["country", "iso_code", "year", "oil_production_twh",
                 "oil_consumption_twh", "oil_prod_per_capita",
                 "oil_cons_change_pct"]
    subset[owid_cols].to_csv(out_path, index=False)
    logger.info("Saved %d OWID rows -> %s", len(subset), out_path)
    return subset


def _worldbank_call(config: dict[str, Any], iso: str) -> list[Any]:
    """Call the World Bank API for one ISO code (or a ';'-joined list)."""
    wb = config["worldbank"]
    url = (config["data_sources"]["worldbank_oil_rents"].format(iso=iso)
           + f"&per_page={wb['per_page']}&date={wb['date_range']}")
    payload = http_get(url, timeout=config["pipeline"]["request_timeout"]).json()
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        raise IngestionError(f"Unexpected World Bank payload: {str(payload)[:300]}")
    return payload


def fetch_worldbank(config: dict[str, Any], out_dir: Path) -> list[Any]:
    """Download oil rents (% of GDP) for all target countries.

    Strategy: one batched multi-country call; if that fails, fall back to one
    call per country; if the API is entirely unavailable and a previously
    downloaded raw JSON exists, reuse it (logged as a warning) so downstream
    stages can still run on the last good snapshot.
    """
    isos = list(config["countries"].keys())
    out_path = out_dir / config["paths"]["raw_files"]["worldbank"]
    try:
        payload = _worldbank_call(config, ";".join(isos))
    except (requests.RequestException, IngestionError, ValueError) as exc:
        logger.warning("Batched World Bank call failed (%s); trying per country", exc)
        records: list[dict[str, Any]] = []
        for iso in isos:
            try:
                records.extend(_worldbank_call(config, iso)[1])
            except (requests.RequestException, IngestionError, ValueError) as err:
                logger.warning("World Bank call failed for %s: %s", iso, err)
        if records:
            payload = [{"page": 1, "pages": 1, "total": len(records)}, records]
        elif out_path.exists():
            logger.warning("World Bank API unavailable; reusing cached %s", out_path)
            with open(out_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        else:
            raise IngestionError("World Bank API unavailable and no cached file") from exc

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    logger.info("Saved %d World Bank records -> %s", len(payload[1]), out_path)
    return payload


def worldbank_to_frame(payload: list[Any]) -> pd.DataFrame:
    """Flatten a World Bank API payload into iso_code/year/oil_rents_pct_gdp."""
    records = [
        {
            "iso_code": rec.get("countryiso3code"),
            "year": int(rec["date"]),
            "oil_rents_pct_gdp": rec.get("value"),
        }
        for rec in payload[1]
        if rec.get("countryiso3code") and str(rec.get("date", "")).isdigit()
    ]
    return pd.DataFrame(records, columns=["iso_code", "year", "oil_rents_pct_gdp"])


def merge_sources(owid: pd.DataFrame, rents: pd.DataFrame,
                  config: dict[str, Any], out_dir: Path) -> pd.DataFrame:
    """Left-join World Bank rents onto OWID rows and save the merged CSV."""
    merged = owid.merge(rents, on=["iso_code", "year"], how="left")
    merged = merged[MERGED_COLUMNS].sort_values(["country", "year"])
    out_path = out_dir / config["paths"]["raw_files"]["merged"]
    merged.to_csv(out_path, index=False)
    logger.info("Saved merged dataset (%d rows) -> %s", len(merged), out_path)
    return merged


def ingest(dry_run: bool = False, config_path: str | None = None) -> dict[str, Any]:
    """Run the full ingestion stage and return a summary dictionary."""
    config = load_config(config_path)
    out_dir = raw_dir(config)
    logger.info("Raw output directory: %s", out_dir)

    if dry_run:
        logger.info("Dry run: config OK, %d countries, years %s-%s, sources: %s",
                    len(config["countries"]), config["years"]["start"],
                    config["years"]["end"], list(config["data_sources"]))
        return {"dry_run": True, "raw_dir": str(out_dir)}

    try:
        owid = fetch_owid(config, out_dir)
        payload = fetch_worldbank(config, out_dir)
        merged = merge_sources(owid, worldbank_to_frame(payload), config, out_dir)
    except (requests.RequestException, IngestionError, ValueError) as exc:
        logger.error("Ingestion failed: %s", exc)
        raise

    summary = {
        "owid_rows": len(owid),
        "worldbank_records": len(payload[1]),
        "merged_rows": len(merged),
        "countries": sorted(merged["iso_code"].unique().tolist()),
    }
    logger.info("Ingestion complete: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="validate configuration without network calls")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    args = parser.parse_args(argv)
    try:
        ingest(dry_run=args.dry_run, config_path=args.config)
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit 1
        logger.exception("Fatal ingestion error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
