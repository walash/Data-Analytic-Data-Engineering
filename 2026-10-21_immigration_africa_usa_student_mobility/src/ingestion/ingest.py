"""Ingestion stage: fetch World Bank indicators for African focus countries + USA.

Every configured indicator is downloaded from the public World Bank
Indicators API v2 (no key required), all result pages are merged and the
output is written to ``data/raw/<file>.json`` using the native World Bank
layout ``[metadata_dict, list_of_records]`` so downstream code can rely on a
single format.

Usage:
    python src/ingestion/ingest.py                # fetch everything
    python src/ingestion/ingest.py --dry-run      # validate config + URLs only
    python src/ingestion/ingest.py --indicator gdp_per_capita
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("ingest")


class WorldBankAPIError(RuntimeError):
    """Raised when the World Bank API returns an error payload."""


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the YAML configuration (CONFIG_PATH env var overrides default)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def country_codes(cfg: dict[str, Any]) -> list[str]:
    """Return the ISO3 codes of the African focus countries plus the USA."""
    codes = list(cfg["countries"]["africa"].keys())
    codes.append(cfg["countries"]["usa"])
    return codes


def build_url(cfg: dict[str, Any], indicator_code: str, countries: list[str]) -> str:
    """Build the World Bank API endpoint for one indicator and many countries."""
    base = cfg["worldbank"]["base_url"].rstrip("/")
    return f"{base}/country/{';'.join(countries)}/indicator/{indicator_code}"


def _make_fetcher(cfg: dict[str, Any]):
    """Create a retrying page fetcher using config-driven retry settings."""
    wb = cfg["worldbank"]

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(wb.get("max_retries", 4))),
        wait=wait_exponential(multiplier=float(wb.get("backoff_seconds", 2)), max=30),
        retry=retry_if_exception_type((requests.RequestException, WorldBankAPIError)),
    )
    def fetch_page(session: requests.Session, url: str, page: int) -> list[Any]:
        params = {
            "format": wb.get("format", "json"),
            "per_page": wb.get("per_page", 1000),
            "date": wb.get("date_range", "2000:2025"),
            "page": page,
        }
        logger.debug("GET %s params=%s", url, params)
        resp = session.get(url, params=params, timeout=wb.get("request_timeout", 60))
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list) or not payload:
            raise WorldBankAPIError(f"Unexpected payload type from {resp.url}")
        if "message" in payload[0]:
            raise WorldBankAPIError(f"API error for {resp.url}: {payload[0]['message']}")
        return payload

    return fetch_page


def fetch_indicator(
    cfg: dict[str, Any],
    indicator_code: str,
    countries: list[str],
    session: requests.Session | None = None,
) -> list[Any]:
    """Fetch all pages for one indicator; return ``[metadata, records]``."""
    session = session or requests.Session()
    fetch_page = _make_fetcher(cfg)
    url = build_url(cfg, indicator_code, countries)

    first = fetch_page(session, url, 1)
    meta = dict(first[0])
    records: list[dict[str, Any]] = list(first[1] or [])
    total_pages = int(meta.get("pages", 1) or 1)
    for page in range(2, total_pages + 1):
        payload = fetch_page(session, url, page)
        records.extend(payload[1] or [])

    meta.update(
        {
            "page": 1,
            "pages_fetched": total_pages,
            "records_fetched": len(records),
            "indicator_code": indicator_code,
            "countries": countries,
            "source_url": url,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    logger.info("%s: %d records across %d page(s)", indicator_code, len(records), total_pages)
    return [meta, records]


def save_json(payload: list[Any], path: Path) -> None:
    """Atomically write JSON to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    tmp.replace(path)


def ingest(
    config_path: str | None = None,
    dry_run: bool = False,
    only: list[str] | None = None,
) -> dict[str, int]:
    """Run ingestion for all (or selected) indicators. Returns record counts."""
    cfg = load_config(config_path)
    raw_dir = PROJECT_ROOT / cfg["paths"]["raw_dir"]
    countries = country_codes(cfg)
    indicators = cfg["worldbank"]["indicators"]

    if only:
        unknown = set(only) - set(indicators)
        if unknown:
            raise ValueError(f"Unknown indicator key(s): {sorted(unknown)}")
        indicators = {k: v for k, v in indicators.items() if k in only}

    results: dict[str, int] = {}
    session = requests.Session()
    session.headers.update({"User-Agent": "africa-usa-student-mobility-pipeline/1.0"})

    for key, spec in indicators.items():
        url = build_url(cfg, spec["code"], countries)
        target = raw_dir / spec["file"]
        if dry_run:
            logger.info("[dry-run] %-22s %s -> %s", key, url, target.relative_to(PROJECT_ROOT))
            results[key] = 0
            continue
        payload = fetch_indicator(cfg, spec["code"], countries, session=session)
        save_json(payload, target)
        results[key] = len(payload[1])
        logger.info("Saved %s (%d records)", target.relative_to(PROJECT_ROOT), results[key])

    logger.info("Ingestion finished: %s", results)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch World Bank indicators to data/raw/")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print URLs, do not download")
    parser.add_argument(
        "--indicator", action="append", dest="indicators",
        help="Indicator key from config (repeatable). Default: all",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    try:
        ingest(args.config, dry_run=args.dry_run, only=args.indicators)
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit code
        logger.error("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
