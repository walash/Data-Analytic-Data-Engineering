"""Ingestion stage: download NCD indicators from the World Bank Indicators API.

Three indicators are fetched for every country and aggregate ("country/all"):

* ``SH.STA.DIAB.ZS`` - Diabetes prevalence (% of population ages 20-79)
* ``SH.DTH.NCOM.ZS`` - Cause of death by non-communicable diseases (% of total)
* ``SH.DYN.NCOM.ZS`` - Mortality from CVD, cancer, diabetes or CRD, ages 30-70 (%)

The API is paginated, so every page is requested until ``pages`` is reached.
For each indicator two files are written to ``data/raw/``:

* ``<name>_worldbank.json`` - the untouched API payload (list of pages)
* ``<name>.csv`` - a flattened table with columns
  ``country_id, country_name, iso3, indicator_id, indicator_name, year, value``

Network calls use exponential-backoff retries (``retry`` package). If the API
stays unreachable and a raw CSV from a previous run exists, it is kept so the
downstream stages can still run.

Usage::

    python src/ingestion/ingest.py                  # fetch all indicators
    python src/ingestion/ingest.py --dry-run        # validate config, no network
    python src/ingestion/ingest.py --indicators diabetes_prevalence --max-pages 1
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from retry.api import retry_call

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
CSV_COLUMNS = [
    "country_id",
    "country_name",
    "iso3",
    "indicator_id",
    "indicator_name",
    "year",
    "value",
]

logger = logging.getLogger("ncd.ingest")


class WorldBankAPIError(RuntimeError):
    """Raised when the World Bank API returns an error payload or bad shape."""


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration file.

    Args:
        path: Optional explicit path. Falls back to ``$CONFIG_PATH`` and then
            ``config/config.yaml`` in the project root.

    Returns:
        Parsed configuration dictionary.
    """
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    for section in ("worldbank", "paths", "pipeline"):
        if section not in config:
            raise KeyError(f"Missing required config section: '{section}'")
    return config


def build_url(config: Dict[str, Any], indicator_code: str) -> str:
    """Build the base indicator endpoint URL (query params added separately)."""
    base = config["worldbank"]["base_url"].rstrip("/")
    return f"{base}/{indicator_code}"


def _get_json(url: str, params: Dict[str, Any], timeout: int) -> Any:
    """Perform one HTTP GET and return decoded JSON (raises on HTTP errors)."""
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    # The API signals errors with a single-element list containing "message"
    if isinstance(payload, list) and len(payload) == 1 and "message" in payload[0]:
        raise WorldBankAPIError(f"World Bank API error: {payload[0]['message']}")
    if not isinstance(payload, list) or len(payload) < 2:
        raise WorldBankAPIError(f"Unexpected payload shape from {url}")
    return payload


def fetch_page(
    url: str, params: Dict[str, Any], pipeline_cfg: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Fetch a single API page with retries and exponential backoff.

    Returns:
        Tuple of (page metadata dict, list of observation records).
    """
    payload = retry_call(
        _get_json,
        fargs=[url, params, int(pipeline_cfg.get("request_timeout_seconds", 60))],
        exceptions=(requests.RequestException, WorldBankAPIError, ValueError),
        tries=int(pipeline_cfg.get("max_retries", 4)),
        delay=float(pipeline_cfg.get("retry_delay_seconds", 2)),
        backoff=float(pipeline_cfg.get("retry_backoff", 2)),
        logger=logger,
    )
    meta, records = payload[0], payload[1] or []
    return meta, records


def fetch_indicator(
    config: Dict[str, Any], indicator_code: str, max_pages: Optional[int] = None
) -> List[Any]:
    """Download every page of an indicator.

    Args:
        config: Pipeline configuration.
        indicator_code: World Bank indicator code, e.g. ``SH.STA.DIAB.ZS``.
        max_pages: Optional cap on the number of pages to request.

    Returns:
        List of raw pages, each in the API's ``[meta, records]`` form.
    """
    wb = config["worldbank"]
    pipeline_cfg = config["pipeline"]
    url = build_url(config, indicator_code)
    page_cap = int(max_pages or pipeline_cfg.get("max_pages", 20))
    pages: List[Any] = []
    page, total_pages = 1, 1
    while page <= min(total_pages, page_cap):
        params = {
            "format": wb.get("format", "json"),
            "per_page": wb.get("per_page", 500),
            "mrv": wb.get("mrv", 10),
            "page": page,
        }
        meta, records = fetch_page(url, params, pipeline_cfg)
        total_pages = int(meta.get("pages", 1))
        logger.info(
            "%s: page %d/%d -> %d records (total=%s)",
            indicator_code, page, total_pages, len(records), meta.get("total"),
        )
        pages.append([meta, records])
        page += 1
    if total_pages > page_cap:
        logger.warning(
            "%s: stopped at page cap %d of %d pages", indicator_code, page_cap, total_pages
        )
    return pages


def flatten_records(pages: List[Any]) -> List[Dict[str, Any]]:
    """Flatten raw API pages into CSV-ready row dictionaries.

    Rows with a missing value are kept (value = empty string) so that the
    transformation stage can make an explicit, logged decision about nulls.
    """
    rows: List[Dict[str, Any]] = []
    for _meta, records in pages:
        for rec in records:
            country = rec.get("country") or {}
            indicator = rec.get("indicator") or {}
            rows.append(
                {
                    "country_id": country.get("id", ""),
                    "country_name": country.get("value", ""),
                    "iso3": rec.get("countryiso3code", ""),
                    "indicator_id": indicator.get("id", ""),
                    "indicator_name": indicator.get("value", ""),
                    "year": rec.get("date", ""),
                    "value": "" if rec.get("value") is None else rec.get("value"),
                }
            )
    return rows


def write_outputs(name: str, pages: List[Any], rows: List[Dict[str, Any]], raw_dir: Path) -> Path:
    """Persist raw JSON pages and the flattened CSV for one indicator."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / f"{name}_worldbank.json"
    csv_path = raw_dir / f"{name}.csv"
    # Store a single page as [meta, records]; multiple pages as a list of pages
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(pages[0] if len(pages) == 1 else pages, fh)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Saved %d rows -> %s (raw JSON: %s)", len(rows), csv_path, json_path.name)
    return csv_path


def ingest(
    config: Dict[str, Any],
    indicators: Optional[List[str]] = None,
    dry_run: bool = False,
    max_pages: Optional[int] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, str]:
    """Run the ingestion stage for the selected indicators.

    Args:
        config: Pipeline configuration.
        indicators: Subset of indicator keys from config; all when ``None``.
        dry_run: Validate configuration and print URLs without any network I/O.
        max_pages: Optional pagination cap (useful for smoke tests).
        output_dir: Override for the raw output directory.

    Returns:
        Mapping of indicator key -> status
        (``fetched`` | ``cached`` | ``dry_run`` | ``failed``).
    """
    available = config["worldbank"]["indicators"]
    selected = indicators or list(available)
    unknown = [k for k in selected if k not in available]
    if unknown:
        raise KeyError(f"Unknown indicator keys: {unknown}. Available: {list(available)}")

    raw_dir = Path(output_dir or config["paths"]["raw_dir"])
    if not raw_dir.is_absolute():
        raw_dir = PROJECT_ROOT / raw_dir

    status: Dict[str, str] = {}
    for key in selected:
        code = available[key]["code"]
        url = build_url(config, code)
        if dry_run:
            logger.info("[dry-run] %s (%s) -> %s -> %s/%s.csv", key, code, url, raw_dir, key)
            status[key] = "dry_run"
            continue
        try:
            pages = fetch_indicator(config, code, max_pages=max_pages)
            rows = flatten_records(pages)
            if not rows:
                raise WorldBankAPIError(f"No records returned for {code}")
            write_outputs(key, pages, rows, raw_dir)
            status[key] = "fetched"
        except (requests.RequestException, WorldBankAPIError, ValueError) as exc:
            existing = raw_dir / f"{key}.csv"
            if existing.exists():
                logger.warning(
                    "%s: API fetch failed (%s); keeping existing raw file %s", key, exc, existing
                )
                status[key] = "cached"
            else:
                logger.error("%s: API fetch failed and no cached file exists: %s", key, exc)
                status[key] = "failed"
    logger.info("Ingestion summary: %s", status)
    return status


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Ingest World Bank NCD indicators.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--indicators", nargs="+", default=None, help="Indicator keys to fetch")
    parser.add_argument("--dry-run", action="store_true", help="Validate config only")
    parser.add_argument("--max-pages", type=int, default=None, help="Cap pages per indicator")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override raw dir")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    config = load_config(args.config)
    setup_logging(os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")))
    status = ingest(
        config,
        indicators=args.indicators,
        dry_run=args.dry_run,
        max_pages=args.max_pages,
        output_dir=args.output_dir,
    )
    if status and all(s == "failed" for s in status.values()):
        logger.error("All indicators failed to ingest.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
