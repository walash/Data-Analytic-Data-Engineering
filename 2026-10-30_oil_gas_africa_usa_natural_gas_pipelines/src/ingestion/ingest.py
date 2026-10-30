"""Ingestion stage: fetch World Bank natural-gas indicators into data/raw/.

For every indicator in ``config/config.yaml`` the module calls the World Bank
Indicators API (all pages), keeps observations for the configured African
countries and the USA, and writes a tidy CSV with the columns
``country_code, country_name, iso3, year, <indicator_key>``.

Network calls are wrapped in exponential-backoff retries (tenacity). If an
indicator cannot be downloaded after all retries, the previously saved raw
file is left untouched so downstream stages can still run.

Usage:
    python src/ingestion/ingest.py            # download all indicators
    python src/ingestion/ingest.py --dry-run  # validate config, no network
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
USER_AGENT = "africa-usa-natural-gas-pipelines/1.0 (data-engineering portfolio)"

logger = logging.getLogger("ingest")


class TransientHTTPError(Exception):
    """Raised for HTTP responses that are worth retrying (429 / 5xx)."""


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load YAML config; ``CONFIG_PATH`` env var overrides the default path."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def target_countries(config: Dict[str, Any]) -> List[str]:
    """Union of African and North American ISO3 codes, order preserved."""
    groups = config.get("countries", {})
    seen: List[str] = []
    for key in ("focus", "africa", "north_america"):
        for code in groups.get(key, []) or []:
            if code not in seen:
                seen.append(code)
    return seen


def raw_dir(config: Dict[str, Any]) -> Path:
    path = PROJECT_ROOT / config["paths"]["raw_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_retrying_get(max_retries: int, backoff: float, timeout: int):
    """Return a GET function with exponential-backoff retry semantics."""

    @retry(
        reraise=True,
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=backoff, min=backoff, max=60),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout, TransientHTTPError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _get(session: requests.Session, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = session.get(url, params=params, timeout=timeout)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientHTTPError(f"HTTP {response.status_code} for {response.url}")
        response.raise_for_status()
        return response.json()

    return _get


def fetch_indicator(session: requests.Session, get_fn, url: str) -> List[Dict[str, Any]]:
    """Download every page of a World Bank indicator endpoint."""
    payload = get_fn(session, url)
    if not isinstance(payload, list) or len(payload) < 2:
        message = payload[0].get("message") if isinstance(payload, list) and payload else payload
        raise ValueError(f"Unexpected World Bank response for {url}: {message}")

    meta, records = payload[0], list(payload[1] or [])
    total_pages = int(meta.get("pages", 1) or 1)
    logger.info("  page 1/%d -> %d records", total_pages, len(records))
    for page in range(2, total_pages + 1):
        page_payload = get_fn(session, url, params={"page": page})
        page_records = page_payload[1] if isinstance(page_payload, list) and len(page_payload) > 1 else []
        records.extend(page_records or [])
        logger.info("  page %d/%d -> %d records", page, total_pages, len(page_records or []))
    return records


def to_rows(records: Iterable[Dict[str, Any]], value_column: str,
            countries: List[str]) -> List[Dict[str, Any]]:
    """Flatten WB records, keep target countries and non-null values."""
    wanted = set(countries)
    rows: List[Dict[str, Any]] = []
    for rec in records:
        iso3 = (rec.get("countryiso3code") or "").strip()
        value = rec.get("value")
        if iso3 not in wanted or value is None:
            continue
        country = rec.get("country") or {}
        rows.append(
            {
                "country_code": country.get("id", ""),
                "country_name": country.get("value", ""),
                "iso3": iso3,
                "year": rec.get("date"),
                value_column: value,
            }
        )
    rows.sort(key=lambda r: (r["country_name"], -int(r["year"])))
    return rows


def write_csv(rows: List[Dict[str, Any]], value_column: str, path: Path) -> None:
    fields = ["country_code", "country_name", "iso3", "year", value_column]
    tmp_path = path.with_suffix(".csv.tmp")
    with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(path)


def ingest(config: Dict[str, Any], dry_run: bool = False) -> Dict[str, int]:
    """Fetch all configured indicators. Returns {indicator_key: rows_written}."""
    pipeline_cfg = config.get("pipeline", {})
    indicators = config["worldbank"]["indicators"]
    countries = target_countries(config)
    out_dir = raw_dir(config)

    logger.info("Target countries (%d): %s", len(countries), ", ".join(countries))
    if dry_run:
        for key, spec in indicators.items():
            logger.info("[dry-run] %s (%s) -> %s", key, spec["url"], out_dir / spec["raw_file"])
        logger.info("[dry-run] configuration valid; no network calls made")
        return {key: 0 for key in indicators}

    get_fn = build_retrying_get(
        max_retries=int(pipeline_cfg.get("max_retries", 5)),
        backoff=float(pipeline_cfg.get("retry_backoff_seconds", 2)),
        timeout=int(pipeline_cfg.get("request_timeout_seconds", 30)),
    )
    results: Dict[str, int] = {}
    failures: List[Tuple[str, str]] = []

    with requests.Session() as session:
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        for key, spec in indicators.items():
            target = out_dir / spec["raw_file"]
            logger.info("Fetching %s [%s]", spec["name"], spec["code"])
            try:
                records = fetch_indicator(session, get_fn, spec["url"])
                rows = to_rows(records, key, countries)
                if not rows:
                    raise ValueError("no observations for the target countries")
                write_csv(rows, key, target)
                (out_dir / f"{target.stem}_meta.json").write_text(
                    json.dumps({"indicator": spec["code"], "source_url": spec["url"],
                                "records_downloaded": len(records), "rows_kept": len(rows)},
                               indent=2),
                    encoding="utf-8",
                )
                results[key] = len(rows)
                logger.info("Saved %d rows -> %s", len(rows), target)
            except (requests.RequestException, TransientHTTPError, ValueError) as exc:
                failures.append((key, str(exc)))
                state = "kept existing file" if target.exists() else "no file available"
                logger.error("Failed to ingest %s: %s (%s)", key, exc, state)
                results[key] = -1

    if failures and all(not (out_dir / indicators[k]["raw_file"]).exists() for k, _ in failures):
        raise RuntimeError(f"Ingestion failed with no fallback data: {failures}")
    return results


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest World Bank natural gas indicators")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without downloading")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO")),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    results = ingest(config, dry_run=args.dry_run)
    logger.info("Ingestion summary: %s", results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
