"""
Ingestion stage - Oil & Gas Refinery Capacity & Fuel Access (Africa vs USA).

Fetches six World Bank Open Data indicators for 14 African countries plus the
United States and writes one tidy CSV per indicator to ``data/raw/``::

    country,iso3,year,<value_column>

Features
--------
* Config-driven (``config/config.yaml``): endpoints, countries, indicators.
* Automatic pagination over the World Bank ``page``/``pages`` metadata.
* Exponential-backoff retries (tenacity) on network errors, 429 and 5xx.
* Atomic writes: a file is only replaced when the new download is non-empty,
  so a transient API outage never wipes previously ingested data.
* ``--dry-run`` validates config and prints the URLs without network I/O.

Usage
-----
    python src/ingestion/ingest.py
    python src/ingestion/ingest.py --dry-run
    python src/ingestion/ingest.py --indicators oil_rents fuel_imports
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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
USER_AGENT = "oil-gas-africa-usa-pipeline/1.0 (+https://data.worldbank.org)"

logger = logging.getLogger("ingest")


class RetryableHTTPError(Exception):
    """Raised for HTTP responses that are worth retrying (429 / 5xx)."""


class WorldBankAPIError(Exception):
    """Raised when the World Bank API returns an error payload."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def setup_logging(level: str = "INFO") -> None:
    """Configure root logging with a consistent, timestamped format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the YAML configuration file.

    Args:
        path: Optional explicit path; falls back to ``$CONFIG_PATH`` and then
            ``config/config.yaml`` in the project root.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If mandatory sections are missing.
    """
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    for section in ("worldbank", "paths", "pipeline"):
        if section not in config:
            raise ValueError(f"Missing '{section}' section in {cfg_path}")
    return config


def build_url(base_url: str, countries: Iterable[str], indicator: str) -> str:
    """Build the World Bank indicator endpoint for a batch of countries.

    Args:
        base_url: API root, e.g. ``https://api.worldbank.org/v2``.
        countries: ISO-2 country codes.
        indicator: World Bank indicator code, e.g. ``NY.GDP.PETR.RT.ZS``.

    Returns:
        Fully qualified endpoint URL (without query string).
    """
    return f"{base_url.rstrip('/')}/country/{';'.join(countries)}/indicator/{indicator}"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------
def make_session() -> requests.Session:
    """Return a ``requests.Session`` with a descriptive User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


def fetch_page(
    session: requests.Session,
    url: str,
    params: Dict[str, Any],
    timeout: int,
    max_retries: int,
    backoff_max: int,
) -> List[Any]:
    """Fetch a single page from the World Bank API with retry/backoff.

    Args:
        session: Shared HTTP session.
        url: Endpoint URL.
        params: Query-string parameters (format, per_page, mrv, page).
        timeout: Per-request timeout in seconds.
        max_retries: Maximum number of attempts.
        backoff_max: Upper bound for exponential wait between attempts.

    Returns:
        The decoded JSON body ``[metadata, records]``.

    Raises:
        WorldBankAPIError: If the API returns an error message payload.
        requests.HTTPError: For non-retryable 4xx responses.
    """

    @retry(
        reraise=True,
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=1, min=1, max=backoff_max),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout, RetryableHTTPError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _call() -> List[Any]:
        response = session.get(url, params=params, timeout=timeout)
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableHTTPError(f"HTTP {response.status_code} for {response.url}")
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list) and payload and isinstance(payload[0], dict) \
                and "message" in payload[0]:
            raise WorldBankAPIError(str(payload[0]["message"]))
        if not isinstance(payload, list) or len(payload) < 2:
            raise WorldBankAPIError(f"Unexpected payload shape from {response.url}")
        return payload

    return _call()


def fetch_indicator(
    session: requests.Session,
    url: str,
    wb_cfg: Dict[str, Any],
    pipe_cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Fetch every page for one indicator and return raw records.

    Args:
        session: Shared HTTP session.
        url: Indicator endpoint.
        wb_cfg: ``worldbank`` config section.
        pipe_cfg: ``pipeline`` config section.

    Returns:
        List of raw World Bank observation dictionaries.
    """
    records: List[Dict[str, Any]] = []
    page, pages = 1, 1
    while page <= pages:
        params = {
            "format": wb_cfg.get("format", "json"),
            "per_page": wb_cfg.get("per_page", 200),
            "mrv": wb_cfg.get("mrv", 10),
            "page": page,
        }
        meta, data = fetch_page(
            session,
            url,
            params,
            timeout=int(pipe_cfg.get("request_timeout_seconds", 30)),
            max_retries=int(pipe_cfg.get("max_retries", 5)),
            backoff_max=int(pipe_cfg.get("backoff_max_seconds", 60)),
        )
        pages = int(meta.get("pages", 1) or 1)
        records.extend(data or [])
        logger.debug("Fetched page %s/%s (%s records)", page, pages, len(data or []))
        page += 1
    return records


# ---------------------------------------------------------------------------
# Parsing & persistence
# ---------------------------------------------------------------------------
def parse_records(records: List[Dict[str, Any]], value_column: str) -> List[Dict[str, Any]]:
    """Convert raw World Bank records into tidy rows, dropping null values.

    Args:
        records: Raw observations from the API.
        value_column: Name of the output value column.

    Returns:
        Rows with keys ``country, iso3, year, <value_column>`` sorted by
        country then descending year (matching the World Bank ordering).
    """
    rows = []
    for rec in records:
        value = rec.get("value")
        iso3 = (rec.get("countryiso3code") or "").strip()
        year = str(rec.get("date", "")).strip()
        if value is None or not iso3 or not year.isdigit():
            continue
        rows.append({
            "country": (rec.get("country") or {}).get("value", iso3),
            "iso3": iso3,
            "year": int(year),
            value_column: float(value),
        })
    rows.sort(key=lambda r: (r["country"], -r["year"]))
    return rows


def write_csv_atomic(rows: List[Dict[str, Any]], path: Path, value_column: str) -> None:
    """Write rows to ``path`` atomically via a temp file + ``os.replace``.

    Args:
        rows: Tidy rows to persist.
        path: Destination CSV path.
        value_column: Name of the value column (defines header order).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["country", "iso3", "year", value_column])
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def ingest(
    config: Optional[Dict[str, Any]] = None,
    indicators: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Run the ingestion stage.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).
        indicators: Optional subset of indicator keys to fetch.
        dry_run: When ``True`` only log the planned requests.

    Returns:
        Mapping of output filename -> number of rows written (``-1`` for dry
        runs, ``0`` when the API returned nothing and the file was kept).

    Raises:
        KeyError: If an unknown indicator key is requested.
        RuntimeError: If every indicator failed to download.
    """
    config = config or load_config()
    wb_cfg, pipe_cfg = config["worldbank"], config["pipeline"]
    raw_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    all_indicators: Dict[str, Dict[str, str]] = wb_cfg["indicators"]
    selected = indicators or list(all_indicators)
    unknown = sorted(set(selected) - set(all_indicators))
    if unknown:
        raise KeyError(f"Unknown indicator keys: {unknown}")

    summary: Dict[str, int] = {}
    failures: List[str] = []
    session = make_session()
    for key in selected:
        spec = all_indicators[key]
        url = build_url(wb_cfg["base_url"], wb_cfg["countries"], spec["code"])
        out_path = raw_dir / spec["file"]
        if dry_run:
            logger.info("[dry-run] %s (%s) -> %s", key, url, out_path.relative_to(PROJECT_ROOT))
            summary[spec["file"]] = -1
            continue
        try:
            logger.info("Fetching %s [%s]", spec.get("description", key), spec["code"])
            rows = parse_records(fetch_indicator(session, url, wb_cfg, pipe_cfg),
                                 spec["value_column"])
        except (requests.RequestException, RetryableHTTPError, WorldBankAPIError,
                ValueError) as exc:
            logger.error("Failed to fetch %s: %s", key, exc)
            failures.append(key)
            continue
        if not rows:
            logger.warning("No data returned for %s; keeping existing %s", key, out_path.name)
            summary[spec["file"]] = 0
            continue
        write_csv_atomic(rows, out_path, spec["value_column"])
        countries = len({r["iso3"] for r in rows})
        logger.info("Saved %s rows (%s countries) -> %s", len(rows), countries, out_path.name)
        summary[spec["file"]] = len(rows)

    if failures and len(failures) == len(selected):
        raise RuntimeError(f"All indicator downloads failed: {failures}")
    if failures:
        logger.warning("Completed with failures: %s", failures)
    return summary


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Ingest World Bank oil & gas indicators.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--indicators", nargs="*", default=None,
                        help="Subset of indicator keys from config (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config and print URLs without downloading")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        setup_logging(config["pipeline"].get("log_level", "INFO"))
        summary = ingest(config, args.indicators, args.dry_run)
    except Exception as exc:  # noqa: BLE001 - surface any failure as exit code 1
        setup_logging()
        logger.exception("Ingestion failed: %s", exc)
        return 1
    logger.info("Ingestion summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
