"""Ingestion stage for the HIV/AIDS Africa & USA pipeline.

Fetches five World Bank indicators (HIV prevalence, ART coverage, HIV
incidence, people living with HIV and current health expenditure) for 15
African countries and the United States, then writes both the raw JSON API
response and a flattened CSV per indicator into ``data/raw/``.

Usage::

    python src/ingestion/ingest.py              # download and save
    python src/ingestion/ingest.py --dry-run    # connectivity check only
    python src/ingestion/ingest.py --config config/config.yaml --log-level DEBUG

The ``--dry-run`` flag performs a lightweight request per indicator, validates
the response structure and exits without writing any files. It is used as the
CI smoke test.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

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

logger = logging.getLogger("hiv_aids.ingest")


class IngestionError(RuntimeError):
    """Raised when the World Bank API returns an unusable response."""


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML configuration (env var ``CONFIG_PATH`` overrides default)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if os.environ.get("WB_API_BASE_URL"):
        config["api"]["base_url"] = os.environ["WB_API_BASE_URL"]
    if os.environ.get("API_TIMEOUT"):
        config["api"]["timeout_seconds"] = int(os.environ["API_TIMEOUT"])
    return config


def country_codes(config: dict[str, Any]) -> list[str]:
    """Return the ISO-2 country codes for all configured countries."""
    groups = config["countries"]
    return [c["code"] for group in ("africa", "usa") for c in groups[group]]


def build_url(config: dict[str, Any], indicator_id: str) -> str:
    """Build the World Bank indicator endpoint URL for all countries."""
    base = config["api"]["base_url"].rstrip("/")
    codes = ";".join(country_codes(config))
    return f"{base}/country/{codes}/indicator/{indicator_id}"


def _make_fetcher(config: dict[str, Any]):
    """Create a retrying GET function configured from ``config['api']``."""
    api = config["api"]

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(api.get("max_retries", 5))),
        wait=wait_exponential(
            multiplier=float(api.get("backoff_multiplier", 2)),
            max=float(api.get("backoff_max_seconds", 60)),
        ),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout, requests.HTTPError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def fetch(session: requests.Session, url: str, params: dict[str, Any]) -> Any:
        response = session.get(url, params=params, timeout=api.get("timeout_seconds", 30))
        if response.status_code >= 500 or response.status_code == 429:
            response.raise_for_status()
        if response.status_code >= 400:
            raise IngestionError(f"HTTP {response.status_code} for {response.url}")
        return response.json()

    return fetch


def validate_payload(payload: Any, indicator_id: str) -> list[dict[str, Any]]:
    """Validate a World Bank payload and return its record list."""
    if not isinstance(payload, list) or not payload:
        raise IngestionError(f"{indicator_id}: unexpected payload type {type(payload)}")
    if isinstance(payload[0], dict) and "message" in payload[0]:
        raise IngestionError(f"{indicator_id}: API error {payload[0]['message']}")
    if len(payload) < 2 or payload[1] is None:
        raise IngestionError(f"{indicator_id}: response contains no records")
    return payload[1]


def fetch_indicator(
    session: requests.Session,
    config: dict[str, Any],
    indicator_id: str,
    per_page: int | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Download every page for one indicator; return (raw payload, records)."""
    fetch = _make_fetcher(config)
    api = config["api"]
    url = build_url(config, indicator_id)
    params = {
        "format": api.get("format", "json"),
        "per_page": per_page or api.get("per_page", 200),
        "mrv": api.get("mrv", 10),
        "page": 1,
    }
    payload = fetch(session, url, params)
    records = list(validate_payload(payload, indicator_id))
    pages = int(payload[0].get("pages", 1) or 1)
    for page in range(2, pages + 1):
        params["page"] = page
        extra = fetch(session, url, params)
        records.extend(validate_payload(extra, indicator_id))
    logger.info("%s: fetched %d records across %d page(s)", indicator_id, len(records), pages)
    return [payload[0], records], records


def flatten_records(records: list[dict[str, Any]], value_column: str) -> list[dict[str, Any]]:
    """Convert World Bank JSON records into flat CSV rows."""
    rows = []
    for rec in records:
        rows.append(
            {
                "country_code": rec["country"]["id"],
                "country_name": rec["country"]["value"],
                "iso3": rec.get("countryiso3code", ""),
                "year": rec["date"],
                "indicator_id": rec["indicator"]["id"],
                "indicator_name": rec["indicator"]["value"],
                value_column: "" if rec.get("value") is None else rec["value"],
            }
        )
    return rows


def write_outputs(raw_dir: Path, key: str, payload: Any, rows: list[dict[str, Any]]) -> None:
    """Persist the raw JSON and flattened CSV for one indicator."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / f"{key}.json"
    csv_path = raw_dir / f"{key}.csv"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Saved %s (%d rows) and %s", csv_path.name, len(rows), json_path.name)


def dry_run(config: dict[str, Any]) -> bool:
    """Check API connectivity and response shape for each indicator."""
    ok = True
    with requests.Session() as session:
        for ind in config["indicators"]:
            try:
                fetch = _make_fetcher(config)
                payload = fetch(
                    session,
                    build_url(config, ind["id"]),
                    {"format": "json", "per_page": 5, "mrv": 1},
                )
                records = validate_payload(payload, ind["id"])
                logger.info("[dry-run] %s reachable (%d sample records, total=%s)",
                            ind["id"], len(records), payload[0].get("total"))
            except (requests.RequestException, IngestionError, ValueError) as exc:
                logger.error("[dry-run] %s failed: %s", ind["id"], exc)
                ok = False
    return ok


def ingest(config: dict[str, Any] | None = None) -> dict[str, int]:
    """Run full ingestion; return a mapping of indicator key -> row count."""
    config = config or load_config()
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    counts: dict[str, int] = {}
    failures: list[str] = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "hiv-aids-africa-usa-pipeline/1.0"})
        for ind in config["indicators"]:
            try:
                payload, records = fetch_indicator(session, config, ind["id"])
                rows = flatten_records(records, ind["value_column"])
                write_outputs(raw_dir, ind["key"], payload, rows)
                counts[ind["key"]] = len(rows)
            except (requests.RequestException, IngestionError, ValueError, KeyError) as exc:
                logger.exception("Failed to ingest %s: %s", ind["id"], exc)
                failures.append(ind["id"])
    if failures:
        raise IngestionError(f"Ingestion failed for indicators: {', '.join(failures)}")
    logger.info("Ingestion complete: %s", counts)
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Ingest World Bank HIV/AIDS indicators.")
    parser.add_argument("--config", default=None, help="Path to config YAML.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate API connectivity without writing files.")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    setup_logging(args.log_level)
    config = load_config(args.config)
    if args.dry_run:
        success = dry_run(config)
        logger.info("Dry run %s", "passed" if success else "FAILED")
        return 0 if success else 1
    try:
        ingest(config)
    except IngestionError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
