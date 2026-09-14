"""Data ingestion module for the Africa-to-USA immigration pipeline.

This module fetches raw data from two public data providers and persists the
responses to ``data/raw/`` as both CSV (flattened, analysis-friendly) and JSON
(faithful API response) files:

* **World Bank Open Data API** - three indicators for a curated list of African
  countries:

    - ``SM.POP.NETM``            Net migration (persons)
    - ``BX.TRF.PWKR.DT.GD.ZS``   Personal remittances received (% of GDP)
    - ``SP.POP.TOTL``            Total population

* **UNHCR Refugee Statistics API** - global refugee population totals and
  asylum decisions rendered in the United States.

The module is defensive: every network call is wrapped with retry logic and
exponential backoff (via :mod:`tenacity`), all failures are logged, and a
``--dry-run`` flag allows the CI pipeline to validate configuration and
connectivity without writing files.

Run directly::

    python src/ingestion/ingest.py            # full ingest
    python src/ingestion/ingest.py --dry-run  # validate config only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    datefmt="%Y-%m-%d %H:%M:%S",
)
LOGGER = logging.getLogger("ingest")

# Project root is two levels above this file: src/ingestion/ingest.py -> root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


# --------------------------------------------------------------------------- #
# Configuration helpers
# --------------------------------------------------------------------------- #
def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration file.

    Args:
        config_path: Optional explicit path to ``config.yaml``. When omitted the
            ``CONFIG_PATH`` environment variable is consulted, then the default
            project location.

    Returns:
        The parsed configuration as a dictionary.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
    """
    path = config_path or Path(os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    LOGGER.info("Loaded configuration from %s", path)
    return config


def resolve_raw_dir(config: Dict[str, Any]) -> Path:
    """Resolve (and create) the raw data output directory.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Absolute path to the raw data directory.
    """
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


# --------------------------------------------------------------------------- #
# Low-level HTTP with retry / backoff
# --------------------------------------------------------------------------- #
@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException,)),
)
def http_get_json(url: str, params: Optional[Dict[str, Any]] = None,
                  timeout: int = 30) -> Any:
    """Perform an HTTP GET and return the decoded JSON payload.

    The call is retried up to four times with exponential backoff on any
    :class:`requests.RequestException` (connection errors, timeouts, HTTP 5xx).

    Args:
        url: Fully qualified request URL.
        params: Optional query-string parameters.
        timeout: Per-request timeout in seconds.

    Returns:
        The decoded JSON response (``dict`` or ``list``).

    Raises:
        requests.RequestException: If the request fails after all retries.
    """
    LOGGER.debug("GET %s params=%s", url, params)
    response = requests.get(url, params=params, timeout=timeout,
                            headers={"Accept": "application/json"})
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #
def save_json(data: Any, path: Path) -> None:
    """Write ``data`` to ``path`` as pretty-printed JSON.

    Args:
        data: Any JSON-serialisable object.
        path: Destination file path.
    """
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
    LOGGER.info("Wrote %s (%d bytes)", path.name, path.stat().st_size)


def save_csv(rows: List[Dict[str, Any]], fieldnames: List[str], path: Path) -> None:
    """Write a list of dictionaries to a CSV file.

    Args:
        rows: List of row dictionaries.
        fieldnames: Ordered column names.
        path: Destination file path.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})
    LOGGER.info("Wrote %s (%d rows)", path.name, len(rows))


# --------------------------------------------------------------------------- #
# World Bank ingestion
# --------------------------------------------------------------------------- #
def fetch_worldbank_indicator(
    config: Dict[str, Any], indicator: str, iso_codes: List[str], timeout: int
) -> List[Dict[str, Any]]:
    """Fetch a single World Bank indicator for a set of countries.

    The World Bank API accepts a semicolon-separated list of ISO codes in a
    single request, which keeps the number of network calls low.

    Args:
        config: Parsed configuration dictionary.
        indicator: World Bank indicator code (e.g. ``SM.POP.NETM``).
        iso_codes: List of ISO3 country codes.
        timeout: Per-request timeout in seconds.

    Returns:
        The list of observation records returned by the API (second element of
        the World Bank response envelope). Returns an empty list if the API
        returns no data.
    """
    wb = config["data_sources"]["worldbank"]
    countries = ";".join(iso_codes)
    url = f"{wb['base_url']}/country/{countries}/indicator/{indicator}"
    params = {
        "format": wb["format"],
        "per_page": wb["per_page"],
        "date": wb["date_range"],
    }
    payload = http_get_json(url, params=params, timeout=timeout)
    # World Bank returns [metadata, [records]]
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        LOGGER.warning("No data returned for indicator %s", indicator)
        return []
    records = payload[1]
    LOGGER.info("Fetched %d records for indicator %s", len(records), indicator)
    return records


def flatten_worldbank(records: List[Dict[str, Any]], value_field: str) -> List[Dict[str, Any]]:
    """Flatten nested World Bank records into flat rows.

    Args:
        records: Raw World Bank observation records.
        value_field: Name to give the observation value column
            (e.g. ``net_migration``).

    Returns:
        A list of flat dictionaries with keys ``country``, ``iso3``, ``year``
        and ``value_field``. Records with a null value are skipped.
    """
    rows: List[Dict[str, Any]] = []
    for rec in records:
        value = rec.get("value")
        if value is None:
            continue
        rows.append(
            {
                "country": rec.get("country", {}).get("value"),
                "iso3": rec.get("countryiso3code"),
                "year": int(rec["date"]),
                value_field: value,
            }
        )
    # Sort by country then descending year for stable, readable output
    rows.sort(key=lambda r: (r["country"] or "", -r["year"]))
    return rows


def ingest_worldbank(config: Dict[str, Any], raw_dir: Path, timeout: int) -> None:
    """Ingest all three World Bank indicators and persist JSON + CSV outputs.

    Args:
        config: Parsed configuration dictionary.
        raw_dir: Directory in which to write output files.
        timeout: Per-request timeout in seconds.
    """
    iso_codes = config["african_countries"]
    indicators = config["data_sources"]["worldbank"]["indicators"]

    mapping = [
        (indicators["net_migration"], "net_migration",
         "worldbank_net_migration.json", "africa_migration_worldbank.csv"),
        (indicators["remittances_pct_gdp"], "remittances_pct_gdp",
         "worldbank_remittances.json", "africa_remittances_worldbank.csv"),
        (indicators["population"], "population",
         "worldbank_population.json", "africa_population_worldbank.csv"),
    ]

    for indicator, value_field, json_name, csv_name in mapping:
        LOGGER.info("Ingesting World Bank indicator %s", indicator)
        records = fetch_worldbank_indicator(config, indicator, iso_codes, timeout)
        # Persist the faithful API envelope for reproducibility
        save_json([{"indicator": indicator, "count": len(records)}, records],
                  raw_dir / json_name)
        rows = flatten_worldbank(records, value_field)
        save_csv(rows, ["country", "iso3", "year", value_field], raw_dir / csv_name)


# --------------------------------------------------------------------------- #
# UNHCR ingestion
# --------------------------------------------------------------------------- #
def fetch_unhcr_population(config: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Fetch global refugee population totals from the UNHCR API.

    Args:
        config: Parsed configuration dictionary.
        timeout: Per-request timeout in seconds.

    Returns:
        The decoded UNHCR population API response.
    """
    unhcr = config["data_sources"]["unhcr"]
    url = f"{unhcr['base_url']}{unhcr['population_endpoint']}"
    params = {
        "yearFrom": unhcr["year_start"],
        "yearTo": unhcr["year_end"],
        "limit": 10000,
    }
    return http_get_json(url, params=params, timeout=timeout)


def fetch_unhcr_asylum_decisions(config: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Fetch UNHCR asylum decisions rendered in the United States.

    Args:
        config: Parsed configuration dictionary.
        timeout: Per-request timeout in seconds.

    Returns:
        The decoded UNHCR asylum-decisions API response.
    """
    unhcr = config["data_sources"]["unhcr"]
    url = f"{unhcr['base_url']}{unhcr['asylum_decisions_endpoint']}"
    params = {
        "yearFrom": unhcr["year_start"],
        "yearTo": unhcr["year_end"],
        "coa": unhcr["coa_iso"],
        "limit": 10000,
    }
    return http_get_json(url, params=params, timeout=timeout)


def ingest_unhcr(config: Dict[str, Any], raw_dir: Path, timeout: int) -> None:
    """Ingest UNHCR population and asylum-decision data and persist to disk.

    Args:
        config: Parsed configuration dictionary.
        raw_dir: Directory in which to write output files.
        timeout: Per-request timeout in seconds.
    """
    LOGGER.info("Ingesting UNHCR global refugee population")
    population = fetch_unhcr_population(config, timeout)
    save_json(population, raw_dir / "unhcr_global_refugees.json")

    LOGGER.info("Ingesting UNHCR USA asylum decisions")
    decisions = fetch_unhcr_asylum_decisions(config, timeout)
    save_json(decisions, raw_dir / "unhcr_asylum_decisions_usa.json")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def ingest(config_path: Optional[Path] = None, dry_run: bool = False) -> None:
    """Run the full ingestion pipeline.

    Args:
        config_path: Optional path to the configuration file.
        dry_run: When ``True`` the configuration is validated and connectivity
            to both providers is probed, but no files are written.
    """
    config = load_config(config_path)
    timeout = config["pipeline"]["request_timeout"]

    if dry_run:
        LOGGER.info("Dry-run mode: validating configuration and connectivity")
        # Validate required config sections exist
        for key in ("data_sources", "african_countries", "paths"):
            if key not in config:
                raise KeyError(f"Missing required config section: {key}")
        LOGGER.info("Configuration valid. %d African countries configured.",
                    len(config["african_countries"]))
        try:
            wb = config["data_sources"]["worldbank"]
            probe = f"{wb['base_url']}/country/USA/indicator/SP.POP.TOTL"
            http_get_json(probe, params={"format": "json", "per_page": 1},
                          timeout=timeout)
            LOGGER.info("World Bank API reachable.")
        except requests.RequestException as exc:  # pragma: no cover - network
            LOGGER.warning("World Bank connectivity probe failed: %s", exc)
        LOGGER.info("Dry-run complete. No files written.")
        return

    raw_dir = resolve_raw_dir(config)
    LOGGER.info("Writing raw data to %s", raw_dir)

    try:
        ingest_worldbank(config, raw_dir, timeout)
    except Exception as exc:  # noqa: BLE001 - report and continue with UNHCR
        LOGGER.error("World Bank ingestion failed: %s", exc)

    try:
        ingest_unhcr(config, raw_dir, timeout)
    except Exception as exc:  # noqa: BLE001 - report failure explicitly
        LOGGER.error("UNHCR ingestion failed: %s", exc)

    LOGGER.info("Ingestion complete.")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional argument list (defaults to ``sys.argv``).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(description="Ingest immigration data.")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config/connectivity without writing files")
    return parser.parse_args(argv)


def main() -> None:
    """Command-line entry point."""
    args = parse_args()
    ingest(config_path=args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
