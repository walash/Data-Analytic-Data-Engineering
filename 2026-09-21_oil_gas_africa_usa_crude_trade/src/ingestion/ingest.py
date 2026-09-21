"""World Bank data ingestion for the Oil & Gas Africa-USA project.

This module fetches seven World Bank development indicators for twelve
countries (eleven major African oil producers plus the USA) and stores each
indicator's raw JSON response under ``data/raw/``.

The World Bank REST API returns a 2-element JSON array per request:
``[metadata_dict, list_of_records]``. We persist the response verbatim so the
downstream transformation stage can parse the canonical structure.

Network calls use a bounded retry strategy with exponential backoff to tolerate
transient failures without hammering the public API.

Run directly with::

    python src/ingestion/ingest.py
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
WORLD_BANK_BASE_URL = "https://api.worldbank.org/v2"

COUNTRIES = [
    "NGA", "AGO", "LBY", "DZA", "GAB", "COG",
    "GNQ", "TCD", "CMR", "GHA", "EGY", "USA",
]

# Mapping of World Bank indicator code -> output filename stem.
INDICATORS: Dict[str, str] = {
    "NY.GDP.PETR.RT.ZS": "wb_oil_rents",
    "NY.GDP.TOTL.RT.ZS": "wb_natural_resources_rents",
    "NY.GDP.PCAP.CD": "wb_gdp_per_capita",
    "EG.USE.PCAP.KG.OE": "wb_energy_use",
    "EG.ELC.PETR.ZS": "wb_electricity_from_oil",
    "TX.VAL.MRCH.CD.WT": "wb_merchandise_exports",
    "BX.KLT.DINV.CD.WD": "wb_fdi_inflows",
}

PER_PAGE = 500
MRV = 10  # most recent 10 years
MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2
REQUEST_TIMEOUT = 60

# Resolve the project root: this file lives at <root>/src/ingestion/ingest.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ingest")


def build_url(indicator_code: str) -> str:
    """Build the World Bank API URL for a single indicator.

    Args:
        indicator_code: World Bank indicator id (e.g. ``NY.GDP.PETR.RT.ZS``).

    Returns:
        Fully-qualified request URL covering all configured countries.
    """
    countries = ";".join(COUNTRIES)
    return (
        f"{WORLD_BANK_BASE_URL}/country/{countries}/indicator/{indicator_code}"
        f"?format=json&per_page={PER_PAGE}&mrv={MRV}"
    )


def fetch_with_retries(url: str) -> Any:
    """Fetch a URL as JSON with bounded retries and exponential backoff.

    Args:
        url: The request URL.

    Returns:
        The parsed JSON payload.

    Raises:
        RuntimeError: If all retry attempts are exhausted.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            # World Bank returns a 2-element list. A dict usually means an error.
            if not isinstance(payload, list) or len(payload) < 2:
                raise ValueError(f"Unexpected payload shape: {type(payload)}")
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Request failed (%s). Retrying in %ds ...", exc, backoff
            )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")


def save_json(payload: Any, filename_stem: str) -> Path:
    """Persist a JSON payload to ``data/raw/<stem>.json``.

    Args:
        payload: The JSON-serialisable object to write.
        filename_stem: Output file stem without extension.

    Returns:
        Path to the written file.
    """
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / f"{filename_stem}.json"
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return out_path


def ingest_indicator(indicator_code: str, filename_stem: str) -> int:
    """Fetch and store one indicator.

    Args:
        indicator_code: World Bank indicator id.
        filename_stem: Output file stem.

    Returns:
        Number of records fetched (may include null-valued records).
    """
    url = build_url(indicator_code)
    payload = fetch_with_retries(url)
    records = payload[1] if isinstance(payload[1], list) else []
    out_path = save_json(payload, filename_stem)
    logger.info(
        "Saved %s: %d records -> %s", indicator_code, len(records), out_path
    )
    return len(records)


def main() -> None:
    """Ingest all configured World Bank indicators for all countries."""
    logger.info("Starting World Bank ingestion for %d indicators", len(INDICATORS))
    total = 0
    for indicator_code, filename_stem in INDICATORS.items():
        try:
            total += ingest_indicator(indicator_code, filename_stem)
        except RuntimeError as exc:
            logger.error("Skipping %s: %s", indicator_code, exc)
    logger.info("Ingestion complete. Total records fetched: %d", total)


if __name__ == "__main__":
    main()
