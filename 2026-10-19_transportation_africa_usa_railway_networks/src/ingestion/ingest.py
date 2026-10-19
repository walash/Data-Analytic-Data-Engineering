"""Ingestion stage for the Africa & USA Railway Networks pipeline.

Fetches five World Bank Indicators API v2 series for all African countries
plus the United States benchmark and writes one tidy CSV per indicator to
``data/raw/``:

* IS.RRS.TOTL.KM     - rail lines (route-km)
* IS.RRS.GOOD.MT.K6  - rail freight (million ton-km)
* IS.RRS.PASG.KM     - rail passengers (million passenger-km)
* NY.GDP.PCAP.CD     - GDP per capita (current US$)
* LP.LPI.INFR.XQ     - LPI infrastructure quality score (1-5)

Every CSV has the columns ``country_code, country_name, year, <value_column>``.
HTTP calls are retried three times with exponential backoff. Run with
``--dry-run`` to validate the configuration and print the request plan
without touching the network or the file system.

Usage::

    python src/ingestion/ingest.py
    python src/ingestion/ingest.py --dry-run
    python src/ingestion/ingest.py --output-dir /tmp/raw --indicator rail_lines_km
"""

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("railway.ingest")


class IngestionError(RuntimeError):
    """Raised when an indicator cannot be downloaded after all retries."""


def load_config(path: Optional[str] = None) -> Dict:
    """Load the YAML configuration (``CONFIG_PATH`` env var overrides the default)."""
    config_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    for key in ("worldbank", "regions", "paths", "pipeline"):
        if key not in config:
            raise ValueError(f"Configuration is missing the '{key}' section")
    return config


def configure_logging(level: str) -> None:
    """Configure root logging once with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", level).upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def country_codes(config: Dict) -> List[str]:
    """Return the de-duplicated list of ISO2 codes across all configured regions."""
    codes: List[str] = []
    for members in config["regions"].values():
        for code in members:
            if code not in codes:
                codes.append(code)
    return codes


def batched(items: List[str], size: int) -> Iterable[List[str]]:
    """Yield successive ``size``-length chunks of ``items``."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


def build_url(config: Dict, codes: List[str], indicator_code: str) -> str:
    """Build the World Bank API URL for a batch of countries and one indicator."""
    base = config["worldbank"]["base_url"].rstrip("/")
    return f"{base}/country/{';'.join(codes)}/indicator/{indicator_code}"


def get_with_retry(session: requests.Session, url: str, params: Dict,
                   max_retries: int, backoff: float, timeout: int) -> list:
    """GET ``url`` and return decoded JSON, retrying with exponential backoff.

    A total of ``max_retries`` retries are attempted after the first call.
    The World Bank API sometimes answers HTTP 200 with an error message
    object; that case is treated as a retryable failure too.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list) and payload and "message" in payload[0]:
                raise IngestionError(f"API error: {payload[0]['message']}")
            if not isinstance(payload, list) or len(payload) < 2:
                raise IngestionError(f"Unexpected payload shape from {response.url}")
            return payload
        except (requests.RequestException, ValueError, IngestionError) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = backoff * (2 ** attempt)
                logger.warning("Attempt %d/%d failed for %s (%s); retrying in %.1fs",
                               attempt + 1, max_retries + 1, url, exc, wait)
                time.sleep(wait)
    raise IngestionError(f"Giving up on {url} after {max_retries + 1} attempts: {last_error}")


def fetch_indicator(session: requests.Session, config: Dict, indicator_code: str,
                    codes: List[str]) -> List[Dict]:
    """Download every page of one indicator for all countries; drop null observations."""
    wb = config["worldbank"]
    pipe = config["pipeline"]
    records: List[Dict] = []
    batches = list(batched(codes, int(wb.get("country_batch_size", 20))))
    failed_batches: List[List[str]] = []
    for batch in batches:
        url = build_url(config, batch, indicator_code)
        page, pages = 1, 1
        try:
            while page <= pages:
                params = {"format": wb["format"], "per_page": wb["per_page"],
                          "date": wb["date_range"], "page": page}
                meta, rows = get_with_retry(session, url, params,
                                            int(pipe["max_retries"]),
                                            float(pipe["backoff_factor_seconds"]),
                                            int(pipe["request_timeout_seconds"]))[:2]
                pages = int(meta.get("pages", 1) or 1)
                for row in rows or []:
                    if row.get("value") is None:
                        continue
                    records.append({
                        "country_code": row["country"]["id"],
                        "country_name": row["country"]["value"],
                        "year": int(row["date"]),
                        "value": float(row["value"]),
                    })
                page += 1
        except IngestionError as exc:
            # Keep the batches that succeeded; a partial extract is still useful downstream.
            logger.error("Batch %s failed for %s: %s", ";".join(batch), indicator_code, exc)
            failed_batches.append(batch)
    if len(failed_batches) == len(batches):
        raise IngestionError(f"All {len(batches)} country batches failed for {indicator_code}")
    if failed_batches:
        missing = sorted(c for b in failed_batches for c in b)
        logger.warning("Indicator %s is partial; missing countries: %s",
                       indicator_code, ",".join(missing))
    logger.info("Indicator %s: %d non-null observations", indicator_code, len(records))
    return records


def write_csv(records: List[Dict], path: Path, value_column: str) -> None:
    """Write records to CSV sorted by country then descending year."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: (r["country_name"], -r["year"]))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["country_code", "country_name", "year", value_column])
        for rec in ordered:
            writer.writerow([rec["country_code"], rec["country_name"], rec["year"], rec["value"]])
    logger.info("Wrote %d rows -> %s", len(ordered), path)


def ingest(config: Dict, output_dir: Optional[Path] = None, dry_run: bool = False,
           only: Optional[List[str]] = None) -> Dict[str, int]:
    """Run the ingestion stage. Returns a mapping of indicator name -> rows written."""
    raw_dir = output_dir or PROJECT_ROOT / config["paths"]["raw_dir"]
    codes = country_codes(config)
    indicators = config["worldbank"]["indicators"]
    selected = {k: v for k, v in indicators.items() if not only or k in only}
    if only and len(selected) != len(set(only)):
        unknown = sorted(set(only) - set(indicators))
        raise ValueError(f"Unknown indicator(s): {unknown}")

    logger.info("Ingesting %d indicators for %d countries into %s",
                len(selected), len(codes), raw_dir)
    summary: Dict[str, int] = {}
    if dry_run:
        for name, spec in selected.items():
            batches = list(batched(codes, int(config["worldbank"].get("country_batch_size", 20))))
            logger.info("[dry-run] %s (%s) -> %s in %d request batch(es), e.g. %s",
                        name, spec["code"], raw_dir / spec["output_file"], len(batches),
                        build_url(config, batches[0], spec["code"]))
            summary[name] = 0
        logger.info("[dry-run] Configuration valid; no network calls made.")
        return summary

    failures = []
    with requests.Session() as session:
        session.headers.update({"User-Agent": "africa-usa-railway-networks/1.0"})
        for name, spec in selected.items():
            try:
                records = fetch_indicator(session, config, spec["code"], codes)
                write_csv(records, raw_dir / spec["output_file"], spec["value_column"])
                summary[name] = len(records)
            except IngestionError as exc:
                logger.error("Failed to ingest %s: %s", name, exc)
                failures.append(name)
    if failures:
        raise IngestionError(f"Ingestion failed for: {', '.join(failures)}")
    return summary


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Fetch World Bank railway indicators.")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--output-dir", help="Override the raw output directory")
    parser.add_argument("--indicator", action="append",
                        help="Only fetch this indicator name (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config and print the request plan without downloading")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = parse_args(argv)
    config = load_config(args.config)
    configure_logging(config["pipeline"].get("log_level", "INFO"))
    output_dir = Path(args.output_dir).resolve() if args.output_dir else None
    try:
        summary = ingest(config, output_dir=output_dir, dry_run=args.dry_run, only=args.indicator)
    except (IngestionError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Ingestion summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
