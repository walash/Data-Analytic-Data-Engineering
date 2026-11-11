"""Ingestion module for the AI-Driven Climate & Environmental Monitoring pipeline.

Fetches five climate/environment indicators from the World Bank Open Data API
for 29 African countries plus the USA and writes one tidy CSV per indicator to
``data/raw/``:

    AG.LND.FRST.ZS     -> forest_area.csv
    EG.FEC.RNEW.ZS     -> renewable_energy.csv
    AG.LND.AGRI.ZS     -> agricultural_land.csv
    EG.USE.ELEC.KH.PC  -> electric_power_consumption.csv
    EG.ELC.ACCS.ZS     -> electricity_access.csv

Every CSV has the columns:
    country_code, country_name, indicator_id, indicator_name, year, value

HTTP calls are retried 3 times with exponential backoff. Use ``--dry-run`` to
validate configuration and print the request URLs without any network access.

Usage:
    python src/ingestion/ingest.py
    python src/ingestion/ingest.py --dry-run
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
OUTPUT_COLUMNS = [
    "country_code", "country_name", "indicator_id",
    "indicator_name", "year", "value",
]

COUNTRIES = (
    "US;NG;KE;ZA;ET;GH;EG;TZ;UG;SN;CM;CI;MZ;ZM;ZW;AO;SD;ML;BF;NE;"
    "RW;MG;BJ;TG;GN;SL;LR;GM;GW;CV"
).split(";")

DEFAULT_INDICATORS = {
    "AG.LND.FRST.ZS": {
        "name": "Forest area (% of land area)",
        "file": "forest_area.csv",
    },
    "EG.FEC.RNEW.ZS": {
        "name": ("Renewable energy consumption "
                 "(% of total final energy consumption)"),
        "file": "renewable_energy.csv",
    },
    "AG.LND.AGRI.ZS": {
        "name": "Agricultural land (% of land area)",
        "file": "agricultural_land.csv",
    },
    "EG.USE.ELEC.KH.PC": {
        "name": "Electric power consumption (kWh per capita)",
        "file": "electric_power_consumption.csv",
    },
    "EG.ELC.ACCS.ZS": {
        "name": "Access to electricity (% of population)",
        "file": "electricity_access.csv",
    },
}

logger = logging.getLogger("ingest")


def load_config(path=None):
    """Load the YAML configuration file.

    Args:
        path: Optional path to the config file. Falls back to the
            ``CONFIG_PATH`` environment variable, then ``config/config.yaml``.

    Returns:
        dict: Parsed configuration (empty dict if the file does not exist).
    """
    config_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    if not config_path.exists():
        logger.warning("Config file %s not found; using defaults", config_path)
        return {}
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def setup_logging(config):
    """Configure root logging from the ``logging`` section of the config.

    Args:
        config: Parsed configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get(
            "format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ),
    )


def build_url(base_url, countries, indicator_id):
    """Build the World Bank API endpoint URL for an indicator.

    Args:
        base_url: API base URL, e.g. ``https://api.worldbank.org/v2``.
        countries: Iterable of ISO-2 country codes.
        indicator_id: World Bank indicator code.

    Returns:
        str: Fully-qualified endpoint URL (query params are added separately).
    """
    country_str = ";".join(countries)
    return f"{base_url.rstrip('/')}/country/{country_str}/indicator/{indicator_id}"


def fetch_with_retry(url, params, max_retries=3, backoff_factor=2, timeout=30):
    """GET a URL and return parsed JSON, retrying with exponential backoff.

    The request is attempted once plus up to ``max_retries`` retries. The
    wait before retry ``n`` (1-based) is ``backoff_factor ** n`` seconds.

    Args:
        url: Endpoint URL.
        params: Query parameters.
        max_retries: Number of retries after the first failure.
        backoff_factor: Base of the exponential backoff.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed JSON payload.

    Raises:
        requests.RequestException: If all attempts fail.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = backoff_factor ** (attempt + 1)
            logger.warning(
                "Request failed (attempt %d/%d): %s. Retrying in %ds",
                attempt + 1, max_retries + 1, exc, wait,
            )
            time.sleep(wait)
    logger.error("All %d attempts failed for %s", max_retries + 1, url)
    if isinstance(last_error, requests.RequestException):
        raise last_error
    raise requests.RequestException(str(last_error))


def fetch_indicator(indicator_id, countries, api_cfg):
    """Fetch all pages of an indicator and return the raw observation records.

    Args:
        indicator_id: World Bank indicator code.
        countries: List of ISO-2 country codes.
        api_cfg: ``world_bank_api`` section of the config.

    Returns:
        list[dict]: Raw observation dictionaries from the API.
    """
    url = build_url(api_cfg.get("base_url", "https://api.worldbank.org/v2"),
                    countries, indicator_id)
    params = {
        "format": "json",
        "per_page": api_cfg.get("per_page", 1000),
        "mrv": api_cfg.get("mrv", 10),
        "page": 1,
    }
    records = []
    while True:
        payload = fetch_with_retry(
            url, params,
            max_retries=api_cfg.get("max_retries", 3),
            backoff_factor=api_cfg.get("backoff_factor", 2),
            timeout=api_cfg.get("timeout_seconds", 30),
        )
        if not isinstance(payload, list) or len(payload) < 2:
            message = payload[0].get("message") if isinstance(payload, list) \
                and payload and isinstance(payload[0], dict) else payload
            raise ValueError(f"Unexpected API response for {indicator_id}: "
                             f"{message}")
        meta, data = payload[0], payload[1] or []
        records.extend(data)
        pages = int(meta.get("pages", 1))
        logger.info("%s: page %d/%d (%d rows)", indicator_id,
                    params["page"], pages, len(data))
        if params["page"] >= pages:
            break
        params["page"] += 1
    return records


def records_to_frame(records, indicator_id, indicator_name):
    """Convert raw API records into the tidy output schema.

    Observations with a null value are dropped.

    Args:
        records: Raw observation dictionaries.
        indicator_id: Indicator code (fallback if missing in records).
        indicator_name: Indicator label (fallback if missing in records).

    Returns:
        pandas.DataFrame: Columns defined by ``OUTPUT_COLUMNS``.
    """
    rows = []
    for rec in records:
        if rec.get("value") is None:
            continue
        rows.append({
            "country_code": rec.get("countryiso3code")
            or (rec.get("country") or {}).get("id"),
            "country_name": (rec.get("country") or {}).get("value"),
            "indicator_id": (rec.get("indicator") or {}).get("id")
            or indicator_id,
            "indicator_name": (rec.get("indicator") or {}).get("value")
            or indicator_name,
            "year": int(rec["date"]),
            "value": float(rec["value"]),
        })
    frame = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return frame.sort_values(["country_name", "year"],
                             ascending=[True, False]).reset_index(drop=True)


def ingest(config, dry_run=False):
    """Run ingestion for every configured indicator.

    Args:
        config: Parsed configuration dictionary.
        dry_run: If True, only log the planned requests and output paths.

    Returns:
        dict: Mapping of indicator code -> number of rows written
        (0 for every indicator in dry-run mode).
    """
    api_cfg = config.get("world_bank_api", {})
    countries = config.get("countries") or COUNTRIES
    indicators = config.get("indicators") or DEFAULT_INDICATORS
    raw_dir = PROJECT_ROOT / config.get("paths", {}).get("raw_data", "data/raw")

    logger.info("Ingesting %d indicators for %d countries into %s",
                len(indicators), len(countries), raw_dir)
    summary = {}
    for indicator_id, meta in indicators.items():
        out_path = raw_dir / meta["file"]
        url = build_url(api_cfg.get("base_url", "https://api.worldbank.org/v2"),
                        countries, indicator_id)
        if dry_run:
            logger.info("[dry-run] %s -> %s?format=json&per_page=%s&mrv=%s",
                        out_path.name, url, api_cfg.get("per_page", 1000),
                        api_cfg.get("mrv", 10))
            summary[indicator_id] = 0
            continue
        raw_dir.mkdir(parents=True, exist_ok=True)
        records = fetch_indicator(indicator_id, countries, api_cfg)
        frame = records_to_frame(records, indicator_id, meta["name"])
        frame.to_csv(out_path, index=False)
        summary[indicator_id] = len(frame)
        logger.info("Wrote %d rows to %s", len(frame), out_path)
    return summary


def main(argv=None):
    """Command-line entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        int: Process exit code (0 on success, 1 on failure).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config and print URLs without fetching")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(config)
    try:
        summary = ingest(config, dry_run=args.dry_run)
    except (requests.RequestException, ValueError) as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    logger.info("Ingestion complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
