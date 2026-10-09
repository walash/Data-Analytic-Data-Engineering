"""Ingestion stage for the AI Healthcare Diagnostics (Africa & USA) pipeline.

Fetches five World Bank health indicators for 14 African countries and the
United States from the public World Bank Indicators API (v2) and stores the
raw JSON responses unchanged in ``data/raw/``:

    SH.XPD.CHEX.PC.CD -> health_expenditure_per_capita.json
    SH.MED.PHYS.ZS    -> physicians_per_1000.json
    SH.DYN.MORT       -> under5_mortality.json
    SH.MED.BEDS.ZS    -> hospital_beds_per_1000.json
    SP.DYN.LE00.IN    -> life_expectancy.json

Each request is retried up to 3 times with exponential backoff. The World
Bank API is free and needs no API key.

Usage:
    python src/ingestion/ingest.py            # download all indicators
    python src/ingestion/ingest.py --dry-run  # print the URLs only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

DEFAULT_COUNTRIES = "NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;US"
DEFAULT_INDICATORS = {
    "SH.XPD.CHEX.PC.CD": "health_expenditure_per_capita",
    "SH.MED.PHYS.ZS": "physicians_per_1000",
    "SH.DYN.MORT": "under5_mortality",
    "SH.MED.BEDS.ZS": "hospital_beds_per_1000",
    "SP.DYN.LE00.IN": "life_expectancy",
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | ingest | %(message)s",
)
logger = logging.getLogger(__name__)


class IngestionError(RuntimeError):
    """Raised when an indicator cannot be fetched after all retries."""


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Load the YAML configuration, falling back to built-in defaults.

    Args:
        path: Location of ``config.yaml``.

    Returns:
        Parsed configuration dictionary (empty dict if the file is missing).
    """
    if not path.exists():
        logger.warning("Config not found at %s; using defaults", path)
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_url(base_url: str, countries: str, indicator: str) -> str:
    """Return the World Bank API endpoint for a set of countries and an indicator."""
    return f"{base_url.rstrip('/')}/country/{countries}/indicator/{indicator}"


def fetch_indicator(
    url: str,
    params: dict[str, Any],
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    timeout: int = 30,
) -> list[Any]:
    """Fetch one indicator, retrying with exponential backoff.

    Args:
        url: World Bank endpoint.
        params: Query-string parameters (format, date, per_page).
        max_retries: Number of retries after the first failed attempt.
        backoff_factor: Base for the exponential wait (``factor ** attempt``).
        timeout: Per-request timeout in seconds.

    Returns:
        The decoded World Bank payload ``[metadata, records]``.

    Raises:
        IngestionError: If every attempt fails or the payload is malformed.
    """
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
                message = payload[0].get("message") if isinstance(payload, list) and payload else payload
                raise ValueError(f"Unexpected World Bank payload: {message}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = backoff_factor ** attempt
                logger.warning(
                    "Attempt %d/%d failed for %s (%s); retrying in %.1fs",
                    attempt + 1, max_retries + 1, url, exc, wait,
                )
                time.sleep(wait)
    raise IngestionError(f"Failed to fetch {url} after {max_retries + 1} attempts: {last_error}")


def save_json(payload: list[Any], path: Path) -> int:
    """Write a payload to disk and return the number of non-null records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return sum(1 for row in payload[1] if row.get("value") is not None)


def ingest(dry_run: bool = False) -> dict[str, int]:
    """Download every configured indicator into ``data/raw/``.

    Args:
        dry_run: When True, only log the URLs that would be requested.

    Returns:
        Mapping of output file stem to number of valid (non-null) records.
    """
    config = load_config()
    base_url = config.get("world_bank_base_url", "https://api.worldbank.org/v2")
    countries_cfg = config.get("countries")
    countries = ";".join(c["code"] for c in countries_cfg) if countries_cfg else DEFAULT_COUNTRIES
    indicators = (
        {code: meta["file"] for code, meta in config["indicators"].items()}
        if config.get("indicators") else DEFAULT_INDICATORS
    )
    pipeline = config.get("pipeline", {})
    raw_dir = Path(os.getenv("RAW_DATA_PATH", PROJECT_ROOT / config.get("paths", {}).get("raw", "data/raw")))
    params = {
        "format": "json",
        "date": pipeline.get("date_range", "2014:2024"),
        "per_page": pipeline.get("per_page", 500),
    }

    summary: dict[str, int] = {}
    failures: list[str] = []
    for code, stem in indicators.items():
        url = build_url(base_url, countries, code)
        if dry_run:
            logger.info("[dry-run] %s -> %s.json", url, stem)
            summary[stem] = 0
            continue
        try:
            payload = fetch_indicator(
                url,
                params,
                max_retries=int(pipeline.get("max_retries", 3)),
                backoff_factor=float(pipeline.get("backoff_factor", 2)),
                timeout=int(pipeline.get("timeout_seconds", 30)),
            )
            valid = save_json(payload, raw_dir / f"{stem}.json")
            summary[stem] = valid
            logger.info("Saved %s.json (%d rows, %d valid)", stem, len(payload[1]), valid)
        except IngestionError as exc:
            logger.error("%s", exc)
            failures.append(code)

    if failures:
        raise IngestionError(f"Indicators failed: {', '.join(failures)}")
    return summary


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Fetch World Bank health indicators.")
    parser.add_argument("--dry-run", action="store_true", help="Log URLs without downloading.")
    args = parser.parse_args()
    try:
        summary = ingest(dry_run=args.dry_run)
    except IngestionError as exc:
        logger.critical("Ingestion failed: %s", exc)
        return 1
    logger.info("Ingestion complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
