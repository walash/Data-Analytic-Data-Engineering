"""Ingestion module for the Africa-USA Maritime Port Trade project.

This module fetches five maritime / trade indicators from the World Bank Open
Data API for 17 African coastal nations plus the USA and persists each indicator
as a tidy CSV in ``data/raw/``.

Indicators fetched (World Bank indicator codes):
    * IS.SHP.GCNW.XQ  -> Liner Shipping Connectivity Index
    * IS.SHP.GOOD.TU  -> Container Port Throughput (TEU)
    * TG.VAL.TOTL.GD.ZS -> Merchandise Trade (% of GDP)
    * LP.LPI.OVRL.XQ  -> Logistics Performance Index (overall score)
    * NE.EXP.GNFS.CD  -> Exports of Goods & Services (current US$)

The World Bank API is public and requires no API key. Each request is retried
up to three times with exponential backoff. The module can also be run with the
``--dry-run`` flag, which validates configuration and connectivity assumptions
without writing any files (used by CI smoke tests).

Usage:
    python src/ingestion/ingest.py            # fetch and write all CSVs
    python src/ingestion/ingest.py --dry-run  # validate only, no downloads
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from typing import Dict, List, Optional

import requests

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a hard dependency at runtime
    yaml = None

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("ingest")

# --------------------------------------------------------------------------- #
# Static configuration (used as fallback when config.yaml is unavailable)
# --------------------------------------------------------------------------- #
BASE_URL = "https://api.worldbank.org/v2"

COUNTRIES: List[str] = [
    "DZA", "AGO", "CMR", "CIV", "EGY", "ETH", "GHA", "KEN", "LBY",
    "MDG", "MAR", "MOZ", "NGA", "SEN", "ZAF", "TZA", "TUN", "USA",
]

# indicator_id -> (output_filename, value_column, mrv)
INDICATORS: Dict[str, Dict[str, object]] = {
    "IS.SHP.GCNW.XQ": {
        "filename": "liner_shipping_connectivity.csv",
        "value_column": "liner_shipping_index",
        "mrv": 10,
    },
    "IS.SHP.GOOD.TU": {
        "filename": "container_port_throughput.csv",
        "value_column": "container_throughput_teu",
        "mrv": 10,
    },
    "TG.VAL.TOTL.GD.ZS": {
        "filename": "merchandise_trade_pct_gdp.csv",
        "value_column": "merchandise_trade_pct_gdp",
        "mrv": 10,
    },
    "LP.LPI.OVRL.XQ": {
        "filename": "logistics_performance_index.csv",
        "value_column": "lpi_score",
        "mrv": 5,
    },
    "NE.EXP.GNFS.CD": {
        "filename": "exports_goods_services_usd.csv",
        "value_column": "exports_usd",
        "mrv": 10,
    },
}

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2
REQUEST_TIMEOUT = 60


def _project_root() -> str:
    """Return the absolute path to the project root directory.

    The project root is two levels above this file (src/ingestion/ingest.py).

    Returns:
        str: Absolute path of the project root.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def load_config() -> Optional[dict]:
    """Load ``config/config.yaml`` if it exists.

    Returns:
        Optional[dict]: Parsed configuration dictionary, or ``None`` when the
        file or the PyYAML dependency is unavailable (the module then falls
        back to the built-in defaults).
    """
    config_path = os.path.join(_project_root(), "config", "config.yaml")
    if yaml is None:
        LOGGER.warning("PyYAML not installed; using built-in default config.")
        return None
    if not os.path.exists(config_path):
        LOGGER.warning("Config file not found at %s; using defaults.", config_path)
        return None
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    LOGGER.info("Loaded configuration from %s", config_path)
    return config


def _raw_data_dir(config: Optional[dict]) -> str:
    """Resolve the absolute raw-data output directory.

    Args:
        config: Optional parsed configuration dictionary.

    Returns:
        str: Absolute path to the raw data directory (created if missing).
    """
    rel = "data/raw"
    if config:
        rel = config.get("paths", {}).get("raw_data_dir", rel)
    raw_dir = os.path.join(_project_root(), rel)
    os.makedirs(raw_dir, exist_ok=True)
    return raw_dir


def fetch_world_bank_indicator(
    indicator_id: str,
    countries: List[str],
    filename: str,
    mrv: int = 10,
    value_column: str = "value",
    raw_dir: Optional[str] = None,
) -> str:
    """Fetch a single indicator from the World Bank API and write it to CSV.

    The function requests the most recent ``mrv`` values for every country in a
    single call, parses the JSON payload, filters out null observations and
    writes a tidy CSV with columns ``country, iso3, year, <value_column>``.

    Retries the HTTP request up to :data:`MAX_RETRIES` times using exponential
    backoff before giving up.

    Args:
        indicator_id: World Bank indicator code (e.g. ``"IS.SHP.GCNW.XQ"``).
        countries: List of ISO-3 country codes to fetch.
        filename: Output CSV filename (written inside the raw data directory).
        mrv: Most-recent-values window (number of years to fetch).
        value_column: Name to give the indicator value column in the CSV.
        raw_dir: Optional override for the raw data output directory.

    Returns:
        str: Absolute path of the CSV file written.

    Raises:
        RuntimeError: If the API cannot be reached after all retries.
    """
    if raw_dir is None:
        raw_dir = _raw_data_dir(None)

    iso_list = ";".join(countries)
    url = f"{BASE_URL}/country/{iso_list}/indicator/{indicator_id}"
    params = {"format": "json", "per_page": 500, "mrv": mrv}

    payload = _request_with_retries(url, params)
    records = _parse_worldbank_payload(payload, value_column)

    output_path = os.path.join(raw_dir, filename)
    _write_csv(records, output_path, value_column)
    LOGGER.info(
        "Wrote %d records for indicator %s -> %s",
        len(records), indicator_id, output_path,
    )
    return output_path


def _request_with_retries(url: str, params: dict) -> list:
    """Perform an HTTP GET with retry and exponential backoff.

    Args:
        url: Fully-qualified request URL.
        params: Query-string parameters.

    Returns:
        list: The decoded JSON body (a list per the World Bank API contract).

    Raises:
        RuntimeError: If all retry attempts fail.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            LOGGER.info("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and data and isinstance(data[0], dict) \
                    and data[0].get("message"):
                raise ValueError(f"World Bank API error: {data[0]['message']}")
            return data
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = BACKOFF_BASE_SECONDS ** attempt
            LOGGER.warning(
                "Request failed (%s). Retrying in %ds ...", exc, wait,
            )
            if attempt < MAX_RETRIES:
                time.sleep(wait)
    raise RuntimeError(
        f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}"
    )


def _parse_worldbank_payload(payload: list, value_column: str) -> List[dict]:
    """Convert a raw World Bank JSON payload into tidy records.

    Args:
        payload: Decoded JSON list ``[metadata, [observations...]]``.
        value_column: Name to assign to the value column.

    Returns:
        List[dict]: Records with keys ``country, iso3, year, <value_column>``.
        Observations whose value is null are skipped.
    """
    records: List[dict] = []
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        LOGGER.warning("Empty or malformed payload received.")
        return records

    for obs in payload[1]:
        value = obs.get("value")
        if value is None:
            continue
        records.append({
            "country": obs.get("country", {}).get("value"),
            "iso3": obs.get("countryiso3code"),
            "year": obs.get("date"),
            value_column: value,
        })
    return records


def _write_csv(records: List[dict], output_path: str, value_column: str) -> None:
    """Write tidy records to a CSV file.

    Uses pandas when available for robust quoting; otherwise falls back to the
    standard library ``csv`` module.

    Args:
        records: List of observation dictionaries.
        output_path: Destination CSV path.
        value_column: Name of the value column (ensures column ordering).
    """
    columns = ["country", "iso3", "year", value_column]
    try:
        import pandas as pd

        frame = pd.DataFrame(records, columns=columns)
        frame.to_csv(output_path, index=False)
    except ImportError:  # pragma: no cover - pandas is a runtime dependency
        import csv

        with open(output_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in records:
                writer.writerow(row)


def fetch_all_data(config: Optional[dict] = None) -> Dict[str, str]:
    """Fetch every configured indicator and write each to a CSV.

    Args:
        config: Optional configuration dictionary. When provided its
            ``world_bank_api`` section drives the countries/indicators;
            otherwise the module defaults are used.

    Returns:
        Dict[str, str]: Mapping of indicator id -> output CSV path.
    """
    raw_dir = _raw_data_dir(config)

    countries = COUNTRIES
    indicators = INDICATORS
    if config:
        wb = config.get("world_bank_api", {})
        countries = wb.get("countries", COUNTRIES)
        cfg_inds = wb.get("indicators")
        if cfg_inds:
            indicators = {
                spec["id"]: {
                    "filename": spec["filename"],
                    "value_column": spec["value_column"],
                    "mrv": spec.get("mrv", 10),
                }
                for spec in cfg_inds.values()
            }

    results: Dict[str, str] = {}
    for indicator_id, spec in indicators.items():
        path = fetch_world_bank_indicator(
            indicator_id=indicator_id,
            countries=countries,
            filename=str(spec["filename"]),
            mrv=int(spec["mrv"]),
            value_column=str(spec["value_column"]),
            raw_dir=raw_dir,
        )
        results[indicator_id] = path
    return results


def _dry_run(config: Optional[dict]) -> None:
    """Validate configuration without performing any network downloads.

    Args:
        config: Optional configuration dictionary.
    """
    countries = COUNTRIES
    indicators = INDICATORS
    if config:
        wb = config.get("world_bank_api", {})
        countries = wb.get("countries", COUNTRIES)
    LOGGER.info("[DRY-RUN] Project root: %s", _project_root())
    LOGGER.info("[DRY-RUN] Raw data dir: %s", _raw_data_dir(config))
    LOGGER.info("[DRY-RUN] %d countries configured.", len(countries))
    LOGGER.info("[DRY-RUN] %d indicators configured:", len(indicators))
    for indicator_id, spec in indicators.items():
        LOGGER.info("[DRY-RUN]   %s -> %s", indicator_id, spec["filename"])
    LOGGER.info("[DRY-RUN] Validation complete. No files were written.")


def main() -> None:
    """Command-line entry point for the ingestion stage."""
    parser = argparse.ArgumentParser(
        description="Ingest maritime/trade indicators from the World Bank API."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration only; do not download or write files.",
    )
    args = parser.parse_args()

    config = load_config()

    if args.dry_run:
        _dry_run(config)
        return

    LOGGER.info("Starting World Bank data ingestion ...")
    results = fetch_all_data(config)
    LOGGER.info("Ingestion complete. %d indicators written.", len(results))


if __name__ == "__main__":
    main()
