"""Ingestion module for the Maternal & Child Health (Africa vs USA) pipeline.

Fetches seven World Bank Open Data indicators for 15 African countries and
the United States and writes one tidy CSV per indicator to ``data/raw/``
(columns: country, country_code, year, value, indicator) plus a combined
JSON file containing the raw API records.

Indicators (World Bank codes):
    SH.STA.MMRT       Maternal mortality ratio (per 100,000 live births)
    SH.DYN.MORT       Under-5 mortality rate (per 1,000 live births)
    SH.DYN.NMRT       Neonatal mortality rate (per 1,000 live births)
    SH.STA.BRTC.ZS    Births attended by skilled health staff (%)
    SH.XPD.CHEX.PC.CD Current health expenditure per capita (US$)
    SH.IMM.IDPT       DPT immunization (% of children 12-23 months)
    SP.DYN.TFRT.IN    Total fertility rate (births per woman)

Usage:
    python src/ingestion/ingest.py              # fetch all indicators
    python src/ingestion/ingest.py --dry-run    # validate config + URLs only
    python src/ingestion/ingest.py --indicators maternal_mortality_ratio
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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
CSV_COLUMNS = ["country", "country_code", "year", "value", "indicator"]

logger = logging.getLogger("ingest")


class TransientAPIError(Exception):
    """Raised for retryable API failures (HTTP 429/5xx, malformed payloads)."""


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent, timestamped format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    """Load the YAML configuration file.

    Args:
        path: Path to ``config.yaml``. ``CONFIG_PATH`` env var overrides it.

    Returns:
        Parsed configuration dictionary.
    """
    env_path = os.environ.get("CONFIG_PATH")
    cfg_path = Path(env_path) if env_path else Path(path)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    logger.debug("Loaded config from %s", cfg_path)
    return config


def resolve_raw_dir(config: Dict[str, Any]) -> Path:
    """Return the raw-data directory, honouring the RAW_DATA_DIR env var."""
    raw = Path(os.environ.get("RAW_DATA_DIR", config["paths"]["raw_dir"]))
    return raw if raw.is_absolute() else PROJECT_ROOT / raw


def build_url(config: Dict[str, Any], indicator_code: str) -> str:
    """Build the World Bank API URL for an indicator across all countries."""
    api = config["api"]["worldbank"]
    countries = ";".join(c["iso2"] for c in config["countries"])
    return api["endpoint_template"].format(
        base_url=api["base_url"].rstrip("/"),
        countries=countries,
        indicator=indicator_code,
    )


def build_session() -> requests.Session:
    """Create an HTTP session with a descriptive User-Agent."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "maternal-child-health-pipeline/1.0 (+data-engineering)",
            "Accept": "application/json",
        }
    )
    return session


def make_fetch_page(config: Dict[str, Any]):
    """Build a retry-wrapped page fetcher using config retry parameters."""
    pipe = config["pipeline"]

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(pipe.get("max_retries", 5))),
        wait=wait_exponential(
            multiplier=1,
            min=float(pipe.get("retry_backoff_min_seconds", 2)),
            max=float(pipe.get("retry_backoff_max_seconds", 30)),
        ),
        retry=retry_if_exception_type(
            (TransientAPIError, requests.ConnectionError, requests.Timeout)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def fetch_page(
        session: requests.Session, url: str, params: Dict[str, Any]
    ) -> List[Any]:
        """Fetch one page of results; raise TransientAPIError when retryable."""
        response = session.get(
            url, params=params, timeout=int(pipe.get("request_timeout_seconds", 30))
        )
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientAPIError(
                f"HTTP {response.status_code} from {response.url}"
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise TransientAPIError(f"Invalid JSON from {response.url}") from exc
        if not isinstance(payload, list) or not payload:
            raise TransientAPIError(f"Unexpected payload shape from {response.url}")
        if isinstance(payload[0], dict) and "message" in payload[0]:
            # World Bank returns [{"message": [...]}] for invalid requests.
            raise ValueError(f"World Bank API error: {payload[0]['message']}")
        return payload

    return fetch_page


def fetch_indicator(
    session: requests.Session,
    config: Dict[str, Any],
    indicator_code: str,
    fetch_page=None,
) -> List[Dict[str, Any]]:
    """Fetch all pages of an indicator and return the raw record list."""
    api = config["api"]["worldbank"]
    url = build_url(config, indicator_code)
    fetch_page = fetch_page or make_fetch_page(config)
    params: Dict[str, Any] = {
        "format": api.get("format", "json"),
        "per_page": api.get("per_page", 1000),
        "mrv": api.get("mrv", 10),
        "page": 1,
    }
    records: List[Dict[str, Any]] = []
    while True:
        payload = fetch_page(session, url, params)
        meta = payload[0]
        data = payload[1] if len(payload) > 1 and payload[1] else []
        records.extend(data)
        pages = int(meta.get("pages", 1) or 1)
        logger.debug(
            "%s page %s/%s -> %d records", indicator_code, params["page"], pages, len(data)
        )
        if params["page"] >= pages:
            break
        params["page"] += 1
        time.sleep(float(config["pipeline"].get("sleep_between_requests_seconds", 0.5)))
    return records


def records_to_rows(records: List[Dict[str, Any]], indicator_name: str) -> List[Dict[str, Any]]:
    """Convert raw API records to tidy rows, dropping null observations."""
    rows: List[Dict[str, Any]] = []
    for rec in records:
        value = rec.get("value")
        if value is None:
            continue
        try:
            year = int(rec.get("date"))
        except (TypeError, ValueError):
            logger.warning("Skipping record with invalid date: %s", rec.get("date"))
            continue
        rows.append(
            {
                "country": (rec.get("country") or {}).get("value", ""),
                "country_code": rec.get("countryiso3code", ""),
                "year": year,
                "value": value,
                "indicator": indicator_name,
            }
        )
    rows.sort(key=lambda r: (r["country"], -r["year"]))
    return rows


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    """Write tidy rows to CSV with a fixed column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def ingest(
    config: Dict[str, Any],
    dry_run: bool = False,
    only: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Run the ingestion step.

    Args:
        config: Parsed configuration.
        dry_run: If True, validate configuration and print the URLs that would
            be requested without performing network calls or writing files.
        only: Optional subset of indicator short names to fetch.

    Returns:
        Mapping of indicator short name to number of rows written
        (0 for every indicator in dry-run mode).
    """
    indicators: Dict[str, Dict[str, Any]] = config["indicators"]
    if only:
        unknown = sorted(set(only) - set(indicators))
        if unknown:
            raise ValueError(f"Unknown indicator(s): {', '.join(unknown)}")
        indicators = {k: v for k, v in indicators.items() if k in only}
    if not config.get("countries"):
        raise ValueError("No countries configured")

    raw_dir = resolve_raw_dir(config)
    results: Dict[str, int] = {}

    if dry_run:
        logger.info("DRY RUN: %d indicators x %d countries", len(indicators), len(config["countries"]))
        for name, meta in indicators.items():
            logger.info("[dry-run] %-36s -> %s", name, build_url(config, meta["code"]))
            logger.info("[dry-run] would write %s", raw_dir / f"{name}.csv")
            results[name] = 0
        logger.info("DRY RUN complete. Configuration is valid.")
        return results

    raw_dir.mkdir(parents=True, exist_ok=True)
    session = build_session()
    fetch_page = make_fetch_page(config)
    combined: Dict[str, List[Dict[str, Any]]] = {}
    failures: List[str] = []

    for name, meta in indicators.items():
        code = meta["code"]
        logger.info("Fetching %s (%s)", name, code)
        try:
            records = fetch_indicator(session, config, code, fetch_page)
        except (requests.RequestException, TransientAPIError, ValueError) as exc:
            logger.error("Failed to fetch %s (%s): %s", name, code, exc)
            failures.append(name)
            continue
        rows = records_to_rows(records, name)
        out_path = raw_dir / f"{name}.csv"
        write_csv(rows, out_path)
        combined[name] = records
        results[name] = len(rows)
        logger.info("Wrote %d rows -> %s", len(rows), out_path)

    if combined:
        json_path = raw_dir / "all_indicators.json"
        existing: Dict[str, Any] = {}
        if json_path.exists() and only:
            with open(json_path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        existing.update(combined)
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
        logger.info("Wrote combined JSON -> %s", json_path)

    if failures:
        raise RuntimeError(f"Ingestion failed for: {', '.join(failures)}")
    logger.info("Ingestion complete: %s", results)
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Fetch World Bank maternal & child health data.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without network calls")
    parser.add_argument("--indicators", nargs="+", help="Subset of indicator short names")
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL"), help="Logging level")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    setup_logging(args.log_level or "INFO")
    try:
        config = load_config(Path(args.config))
        if not args.log_level:
            logging.getLogger().setLevel(config["pipeline"].get("log_level", "INFO"))
        ingest(config, dry_run=args.dry_run, only=args.indicators)
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
