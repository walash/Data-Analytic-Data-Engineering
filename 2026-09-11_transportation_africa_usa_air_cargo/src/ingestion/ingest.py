"""Data ingestion module for the Africa-USA Air Cargo & Freight Routes project.

This module fetches raw source data from three public providers and persists the
results to the ``data/raw`` directory so that downstream transformation and
loading stages can operate on a stable local snapshot.

Sources:
    * OurAirports  -- global airport reference data (CSV).
    * OpenFlights  -- global airline route network (routes.dat) and airport
      reference data (airports.dat).
    * World Bank   -- air transport freight (IS.AIR.GOOD.MT.K1) and air
      passengers carried (IS.AIR.PSGR) indicators via the public REST API.

The module can be run directly::

    python src/ingestion/ingest.py            # perform a full download
    python src/ingestion/ingest.py --dry-run  # validate config / connectivity

The ``--dry-run`` flag performs no network writes to disk; it only verifies that
configuration loads correctly and that the output directory can be created. It is
used by the CI smoke-test job.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Optional

import requests
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

# --------------------------------------------------------------------------- #
# Logging configuration
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("ingest")

# Project root is two levels up from this file (src/ingestion/ingest.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Sensible fallback URLs used if the config file cannot be read. These mirror the
# values stored in config/config.yaml.
DEFAULT_SOURCES = {
    "ourairports_airports": (
        "https://davidmegginson.github.io/ourairports-data/airports.csv"
    ),
    "openflights_routes": (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/"
        "data/routes.dat"
    ),
    "openflights_airports": (
        "https://raw.githubusercontent.com/jpatokal/openflights/master/"
        "data/airports.dat"
    ),
    "worldbank_air_freight": (
        "https://api.worldbank.org/v2/country/all/indicator/"
        "IS.AIR.GOOD.MT.K1?format=json&per_page=500"
    ),
    "worldbank_air_passengers": (
        "https://api.worldbank.org/v2/country/all/indicator/"
        "IS.AIR.PSGR?format=json&per_page=500"
    ),
}

REQUEST_TIMEOUT = 60  # seconds


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the YAML project configuration.

    Args:
        config_path: Path to the ``config.yaml`` file.

    Returns:
        A dictionary with the parsed configuration. If the file is missing or
        cannot be parsed, a minimal dictionary built from ``DEFAULT_SOURCES`` is
        returned so that ingestion can still proceed.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        LOGGER.info("Loaded configuration from %s", config_path)
        return config or {}
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning(
            "Could not load config at %s (%s); using built-in defaults.",
            config_path,
            exc,
        )
        return {
            "data_sources": DEFAULT_SOURCES,
            "paths": {"raw": "data/raw"},
        }


def resolve_paths(config: dict) -> Path:
    """Resolve and create the raw data output directory.

    Args:
        config: Parsed project configuration.

    Returns:
        Absolute :class:`~pathlib.Path` to the raw data directory.
    """
    raw_rel = config.get("paths", {}).get("raw", "data/raw")
    raw_dir = (PROJECT_ROOT / raw_rel).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    LOGGER.info("Raw data directory: %s", raw_dir)
    return raw_dir


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _download(url: str) -> bytes:
    """Download the content at ``url`` with automatic exponential-backoff retry.

    Args:
        url: Fully-qualified HTTP(S) URL to fetch.

    Returns:
        The raw response body as bytes.

    Raises:
        requests.RequestException: If all retry attempts are exhausted.
    """
    LOGGER.info("Requesting %s", url)
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    LOGGER.info("Received %d bytes from %s", len(response.content), url)
    return response.content


def fetch_to_file(url: str, destination: Path, dry_run: bool = False) -> bool:
    """Fetch a single URL and write it to ``destination``.

    Args:
        url: Source URL.
        destination: Local file path to write the downloaded bytes to.
        dry_run: If ``True``, no network request is made and no file is written.

    Returns:
        ``True`` on success (or on a successful dry-run check), ``False`` if the
        download failed after exhausting retries.
    """
    if dry_run:
        LOGGER.info("[dry-run] Would download %s -> %s", url, destination.name)
        return True
    try:
        payload = _download(url)
    except requests.RequestException as exc:
        LOGGER.error("Failed to download %s: %s", url, exc)
        return False
    destination.write_bytes(payload)
    LOGGER.info("Saved %s", destination)
    return True


def ingest(config: Optional[dict] = None, dry_run: bool = False) -> dict:
    """Run the full ingestion process for all configured data sources.

    Args:
        config: Optional pre-loaded configuration. When ``None`` the default
            config file is loaded.
        dry_run: If ``True``, connectivity/config is validated but nothing is
            downloaded or written.

    Returns:
        A mapping of source name to a boolean success flag.
    """
    config = config or load_config()
    raw_dir = resolve_paths(config)
    sources = config.get("data_sources", DEFAULT_SOURCES)

    # Map logical source names to their output filenames.
    output_map = {
        "ourairports_airports": "airports_ourairports.csv",
        "openflights_routes": "openflights_routes.dat",
        "openflights_airports": "openflights_airports.dat",
        "worldbank_air_freight": "worldbank_air_freight.json",
        "worldbank_air_passengers": "worldbank_air_passengers.json",
    }

    results: dict = {}
    for name, filename in output_map.items():
        url = sources.get(name, DEFAULT_SOURCES.get(name))
        if not url:
            LOGGER.warning("No URL configured for source '%s'; skipping.", name)
            results[name] = False
            continue
        destination = raw_dir / filename
        results[name] = fetch_to_file(url, destination, dry_run=dry_run)

    ok = sum(1 for v in results.values() if v)
    LOGGER.info("Ingestion finished: %d/%d sources succeeded.", ok, len(results))
    if dry_run:
        LOGGER.info("[dry-run] Completed configuration and path validation.")
    return results


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional list of arguments (used mainly for testing).

    Returns:
        Parsed :class:`argparse.Namespace`.
    """
    parser = argparse.ArgumentParser(
        description="Ingest raw air-cargo source data into data/raw."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and paths without downloading data.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config.yaml.",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Entry point for command-line execution.

    Returns:
        Process exit code: ``0`` on success, ``1`` if any non-dry-run source
        failed to download.
    """
    args = parse_args()
    config = load_config(Path(args.config))
    results = ingest(config=config, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
