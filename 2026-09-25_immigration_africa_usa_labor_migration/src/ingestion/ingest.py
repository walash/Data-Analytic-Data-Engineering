"""Ingestion module for the Africa-to-USA Labor Migration & Remittances pipeline.

This module fetches six labor-migration and economic indicators from the public
World Bank Indicators API and persists each as a CSV file in ``data/raw/``.

Indicators fetched:
    * SM.POP.NETM         - Net migration
    * BX.TRF.PWKR.CD.DT   - Personal remittances received (current US$)
    * BX.TRF.PWKR.DT.GD.ZS- Personal remittances received (% of GDP)
    * NY.GDP.PCAP.CD      - GDP per capita (current US$)
    * SL.UEM.TOTL.ZS      - Unemployment, total (% of labor force)
    * SP.POP.TOTL         - Population, total

The module implements retry logic with exponential backoff, structured logging,
and a ``--dry-run`` mode used by CI smoke tests. It can be executed directly or
imported and driven via :func:`ingest`.
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from typing import Any, Dict, List

import requests
import yaml

LOGGER = logging.getLogger("ingestion")

# Resolve project root two levels up from this file (src/ingestion/ -> project)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "config.yaml")

CSV_HEADER = [
    "country_id",
    "country_name",
    "indicator_id",
    "indicator_name",
    "year",
    "value",
]


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent, timestamped format.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(config_path: str = DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load and parse the YAML configuration file.

    Args:
        config_path: Absolute or relative path to ``config.yaml``.

    Returns:
        Parsed configuration as a dictionary.
    """
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_raw_dir(config: Dict[str, Any]) -> str:
    """Resolve (and create) the raw-data output directory.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Absolute path to the raw-data directory.
    """
    raw_dir = os.path.join(PROJECT_ROOT, config["paths"]["raw_data_dir"])
    os.makedirs(raw_dir, exist_ok=True)
    return raw_dir


def build_url(config: Dict[str, Any], indicator_code: str, mrv: int) -> str:
    """Construct a World Bank API request URL for a given indicator.

    Args:
        config: Parsed configuration dictionary.
        indicator_code: World Bank indicator code (e.g. ``"SM.POP.NETM"``).
        mrv: Number of most-recent values to request per country.

    Returns:
        Fully-formed request URL.
    """
    api = config["api"]
    countries = ";".join(api["countries"])
    return (
        f"{api['base_url']}/country/{countries}/indicator/{indicator_code}"
        f"?format={api['response_format']}&per_page={api['per_page']}&mrv={mrv}"
    )


def fetch_json(url: str, retry_count: int, retry_delay: int, timeout: int) -> List[Any]:
    """Fetch JSON from a URL with retries and exponential backoff.

    Args:
        url: Request URL.
        retry_count: Maximum number of attempts.
        retry_delay: Base delay in seconds; doubled after each failed attempt.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed JSON payload (a list, per the World Bank API contract).

    Raises:
        RuntimeError: If all attempts fail.
    """
    delay = retry_delay
    last_error: Exception | None = None
    for attempt in range(1, retry_count + 1):
        try:
            LOGGER.info("GET %s (attempt %d/%d)", url, attempt, retry_count)
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            LOGGER.warning("Attempt %d failed: %s", attempt, error)
            if attempt < retry_count:
                LOGGER.info("Backing off for %d seconds", delay)
                time.sleep(delay)
                delay *= 2
    raise RuntimeError(f"Failed to fetch {url} after {retry_count} attempts: {last_error}")


def parse_records(payload: List[Any]) -> List[List[Any]]:
    """Flatten a World Bank JSON payload into CSV-ready rows.

    Args:
        payload: JSON payload returned by the World Bank API. Element ``[0]`` is
            metadata and element ``[1]`` holds the observation records.

    Returns:
        A list of rows matching :data:`CSV_HEADER`.
    """
    if not payload or len(payload) < 2 or not payload[1]:
        return []
    rows: List[List[Any]] = []
    for item in payload[1]:
        rows.append(
            [
                item.get("countryiso3code", ""),
                (item.get("country") or {}).get("value", ""),
                (item.get("indicator") or {}).get("id", ""),
                (item.get("indicator") or {}).get("value", ""),
                item.get("date", ""),
                item.get("value", ""),
            ]
        )
    return rows


def write_csv(rows: List[List[Any]], output_path: str) -> None:
    """Write rows to a CSV file with the standard header.

    Args:
        rows: Data rows to write.
        output_path: Destination file path.
    """
    with open(output_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def ingest(config_path: str = DEFAULT_CONFIG_PATH, dry_run: bool = False) -> Dict[str, int]:
    """Fetch every configured indicator and persist it as a CSV file.

    Args:
        config_path: Path to the YAML configuration file.
        dry_run: If ``True``, only validate configuration and URL construction
            without performing network requests or writing files.

    Returns:
        Mapping of output filename -> number of rows written (0 for dry runs).
    """
    config = load_config(config_path)
    configure_logging(config["pipeline"].get("log_level", "INFO"))
    raw_dir = resolve_raw_dir(config)

    pipeline = config["pipeline"]
    retry_count = pipeline["retry_count"]
    retry_delay = pipeline["retry_delay"]
    timeout = pipeline["request_timeout"]

    results: Dict[str, int] = {}
    for name, meta in config["api"]["indicators"].items():
        url = build_url(config, meta["code"], meta["mrv"])
        filename = meta["filename"]
        if dry_run:
            LOGGER.info("[dry-run] Would fetch %s -> %s", meta["code"], filename)
            LOGGER.info("[dry-run] URL: %s", url)
            results[filename] = 0
            continue

        payload = fetch_json(url, retry_count, retry_delay, timeout)
        rows = parse_records(payload)
        output_path = os.path.join(raw_dir, filename)
        write_csv(rows, output_path)
        LOGGER.info("Wrote %d rows to %s", len(rows), output_path)
        results[filename] = len(rows)

    LOGGER.info("Ingestion complete: %s", results)
    return results


def main() -> None:
    """Command-line entry point for the ingestion stage."""
    parser = argparse.ArgumentParser(description="World Bank labor-migration data ingestion")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to config.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and URLs without downloading data",
    )
    args = parser.parse_args()
    ingest(config_path=args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
