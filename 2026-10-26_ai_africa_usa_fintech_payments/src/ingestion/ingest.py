"""Ingestion stage of the AI-Driven Fintech & Digital Payments (Africa + USA) pipeline.

Fetches five World Bank development indicators that together describe the
digital-finance landscape of 15 African countries and the United States:

* FX.OWN.TOTL.ZS     - account ownership (financial inclusion, % of adults 15+)
* IT.NET.USER.ZS     - individuals using the Internet (% of population)
* IT.CEL.SETS.P2     - mobile cellular subscriptions (per 100 people)
* BX.TRF.PWKR.CD.DT  - personal remittances received (current US$)
* NY.GDP.PCAP.CD     - GDP per capita (current US$)

plus the supplementary SP.POP.TOTL (population) series, which the
transformation stage needs to turn GDP per capita into total GDP when
computing the remittance-to-GDP ratio.

Each indicator is requested from the public World Bank Indicators API, e.g.::

    https://api.worldbank.org/v2/country/NG;KE;...;US/indicator/FX.OWN.TOTL.ZS?format=json&per_page=500&mrv=5

and written to ``data/raw/<indicator>.csv`` with the columns
``country_code, country_name, indicator, year, value`` (``value_usd`` for
currency-denominated series). Requests are retried up to 3 times with
exponential backoff. Files are written atomically, so a failed download never
overwrites a previously good snapshot.

Usage::

    python src/ingestion/ingest.py              # download everything
    python src/ingestion/ingest.py --dry-run    # validate config and print URLs only
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("ingest")


class IngestionError(RuntimeError):
    """Raised when an indicator cannot be downloaded after all retries."""


def load_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML pipeline configuration.

    Args:
        config_path: Optional explicit path. Defaults to ``$CONFIG_PATH`` or
            ``config/config.yaml`` at the project root.

    Returns:
        The parsed configuration dictionary.
    """
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    if "world_bank_api" not in config or "paths" not in config:
        raise ValueError(f"Config {path} is missing 'world_bank_api' or 'paths' section")
    return config


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a timestamped format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def country_list(config: Dict[str, Any]) -> List[str]:
    """Return the ISO-2 country codes (Africa first, then USA) from the config."""
    countries = config["world_bank_api"]["countries"]
    return list(countries.get("africa", [])) + list(countries.get("usa", []))


def build_url(base_url: str, countries: List[str], indicator_code: str) -> str:
    """Build the World Bank API endpoint URL for one indicator.

    Args:
        base_url: API root, e.g. ``https://api.worldbank.org/v2``.
        countries: ISO-2 country codes joined with ``;`` in the path.
        indicator_code: World Bank indicator id, e.g. ``FX.OWN.TOTL.ZS``.

    Returns:
        The endpoint URL without query string.
    """
    return f"{base_url.rstrip('/')}/country/{';'.join(countries)}/indicator/{indicator_code}"


def fetch_with_retry(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    timeout: int = 30,
    retry_on_status: Optional[List[int]] = None,
) -> Any:
    """GET a JSON payload, retrying transient failures with exponential backoff.

    A total of ``max_retries + 1`` attempts is made. Between attempts the
    function sleeps ``backoff_factor ** attempt`` seconds (2s, 4s, 8s by default).

    Raises:
        IngestionError: If every attempt fails or the payload is not valid JSON.
    """
    retry_on_status = retry_on_status or [429, 500, 502, 503, 504]
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            if response.status_code in retry_on_status:
                raise requests.HTTPError(f"Retryable HTTP {response.status_code}", response=response)
            response.raise_for_status()
            return response.json()
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError, ValueError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status not in retry_on_status and 400 <= status < 500:
                raise IngestionError(f"Non-retryable HTTP {status} for {url}") from exc
            if attempt < max_retries:
                wait = backoff_factor ** (attempt + 1)
                logger.warning("Attempt %d/%d failed for %s (%s); retrying in %.0fs",
                               attempt + 1, max_retries + 1, url, exc, wait)
                time.sleep(wait)
    raise IngestionError(f"Failed after {max_retries + 1} attempts: {url} ({last_error})")


def fetch_indicator(
    session: requests.Session,
    config: Dict[str, Any],
    indicator_code: str,
) -> List[Dict[str, Any]]:
    """Download every page of one indicator and return the raw observation dicts.

    The World Bank API returns ``[metadata, observations]``; an error payload
    is a single-element list containing a ``message`` key.
    """
    api = config["world_bank_api"]
    retry = api.get("retry", {})
    url = build_url(api["base_url"], country_list(config), indicator_code)
    page, pages, records = 1, 1, []
    while page <= pages:
        params = {"format": api.get("format", "json"), "per_page": api.get("per_page", 500),
                  "mrv": api.get("mrv", 5), "page": page}
        payload = fetch_with_retry(
            session, url, params,
            max_retries=int(retry.get("max_retries", 3)),
            backoff_factor=float(retry.get("backoff_factor", 2)),
            timeout=int(api.get("timeout_seconds", 30)),
            retry_on_status=retry.get("retry_on_status"),
        )
        if not isinstance(payload, list) or not payload:
            raise IngestionError(f"Unexpected payload for {indicator_code}: {str(payload)[:200]}")
        if "message" in payload[0]:
            raise IngestionError(f"World Bank API error for {indicator_code}: {payload[0]['message']}")
        pages = int(payload[0].get("pages", 1) or 1)
        records.extend(payload[1] if len(payload) > 1 and payload[1] else [])
        page += 1
    logger.info("Fetched %d observations for %s", len(records), indicator_code)
    return records


def records_to_rows(records: List[Dict[str, Any]], indicator_code: str) -> List[Dict[str, Any]]:
    """Flatten API observations into CSV rows, dropping null values.

    Returns rows with keys ``country_code`` (ISO-3), ``country_name``,
    ``indicator``, ``year`` and ``value`` sorted by country then year (desc).
    """
    rows = []
    for rec in records:
        if rec.get("value") is None:
            continue
        rows.append({
            "country_code": rec.get("countryiso3code") or rec.get("country", {}).get("id"),
            "country_name": rec.get("country", {}).get("value"),
            "indicator": indicator_code,
            "year": int(rec["date"]),
            "value": float(rec["value"]),
        })
    rows.sort(key=lambda r: (r["country_code"], -r["year"]))
    return rows


def write_csv_atomic(rows: List[Dict[str, Any]], path: Path, value_column: str) -> None:
    """Write rows to ``path`` via a temporary file, renaming ``value`` to ``value_column``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    fields = ["country_code", "country_name", "indicator", "year", value_column]
    with open(tmp_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {k: row[k] for k in fields[:-1]}
            out[value_column] = row["value"]
            writer.writerow(out)
    os.replace(tmp_path, path)


def all_indicators(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Merge core and supplementary indicator definitions into one mapping."""
    api = config["world_bank_api"]
    merged = dict(api.get("indicators", {}))
    merged.update(api.get("supplementary_indicators", {}) or {})
    return merged


def ingest(config: Dict[str, Any], dry_run: bool = False) -> Dict[str, int]:
    """Download every configured indicator into the raw data directory.

    Args:
        config: Parsed pipeline configuration.
        dry_run: When True, only validate config and log the URLs to be called.

    Returns:
        Mapping of indicator key -> number of rows written (0 in dry-run mode).

    Raises:
        IngestionError: If one or more core indicators failed to download.
    """
    raw_dir = PROJECT_ROOT / os.environ.get("RAW_DATA_DIR", config["paths"]["raw_data_dir"])
    indicators = all_indicators(config)
    countries = country_list(config)
    logger.info("Ingesting %d indicators for %d countries into %s", len(indicators), len(countries), raw_dir)

    results: Dict[str, int] = {}
    failures: List[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": "fintech-africa-usa-pipeline/1.0"})

    for key, spec in indicators.items():
        url = build_url(config["world_bank_api"]["base_url"], countries, spec["code"])
        if dry_run:
            logger.info("[dry-run] %-22s -> %s -> %s", key, url, raw_dir / spec["file"])
            results[key] = 0
            continue
        try:
            rows = records_to_rows(fetch_indicator(session, config, spec["code"]), spec["code"])
            if not rows:
                raise IngestionError(f"No non-null observations returned for {spec['code']}")
            write_csv_atomic(rows, raw_dir / spec["file"], spec.get("value_column", "value"))
            results[key] = len(rows)
            logger.info("Saved %d rows -> %s", len(rows), raw_dir / spec["file"])
        except IngestionError as exc:
            logger.error("Indicator %s failed: %s", key, exc)
            failures.append(key)

    session.close()
    core_failures = [f for f in failures if f in config["world_bank_api"].get("indicators", {})]
    if core_failures:
        raise IngestionError(f"Core indicators failed: {', '.join(core_failures)}")
    if failures:
        logger.warning("Supplementary indicators failed (pipeline continues): %s", ", ".join(failures))
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Ingest World Bank fintech indicators (Africa + USA).")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and print URLs without downloading.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    config = load_config(args.config)
    setup_logging(os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO")))
    try:
        results = ingest(config, dry_run=args.dry_run)
    except IngestionError as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1
    logger.info("Ingestion complete: %s", results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
