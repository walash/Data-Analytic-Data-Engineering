"""Ingestion stage for the Africa-USA Road Infrastructure & Trade Corridors pipeline.

Fetches seven World Bank development indicators (logistics performance,
road infrastructure, trade and income) for 15 African countries plus the
USA benchmark, and writes both the raw JSON response and a tidy CSV
(country_code, country_name, indicator, year, value) to ``data/raw/``.

Usage:
    python src/ingestion/ingest.py            # download everything
    python src/ingestion/ingest.py --dry-run  # validate config, print URLs
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is optional for dry-run
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

BASE_URL = "https://api.worldbank.org/v2"
COUNTRIES = [
    "NG", "GH", "KE", "ET", "ZA", "EG", "MA", "TZ",
    "UG", "SN", "CM", "CI", "ZM", "MZ", "RW", "USA",
]
INDICATORS = {
    "lpi_overall": {"code": "LP.LPI.OVRL.XQ", "name": "LPI_Overall_Score", "mrv": 5},
    "lpi_infrastructure": {"code": "LP.LPI.INFR.XQ", "name": "LPI_Infrastructure_Score", "mrv": 5},
    "road_density": {"code": "IS.ROD.DNST.K2", "name": "Road_Density_km_per_100sqkm", "mrv": 10},
    "paved_roads_pct": {"code": "IS.ROD.PAVE.ZS", "name": "Paved_Roads_Pct", "mrv": 10},
    "merchandise_exports": {"code": "TX.VAL.MRCH.CD.WT", "name": "Merchandise_Exports_USD", "mrv": 5},
    "trade_pct_gdp": {"code": "NE.TRD.GNFS.ZS", "name": "Trade_Pct_GDP", "mrv": 5},
    "gdp_per_capita": {"code": "NY.GDP.PCAP.CD", "name": "GDP_Per_Capita_USD", "mrv": 5},
}
MAX_RETRIES = 3
BACKOFF_BASE = 2
TIMEOUT = 30
RETRY_STATUS = {429, 500, 502, 503, 504}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest")


def load_config(path=CONFIG_PATH):
    """Load the YAML config, falling back to module defaults if unavailable.

    Args:
        path: Path to ``config/config.yaml``.

    Returns:
        dict with keys ``base_url``, ``countries``, ``indicators``,
        ``max_retries``, ``backoff_base``, ``timeout`` and ``raw_dir``.
    """
    settings = {
        "base_url": BASE_URL,
        "countries": COUNTRIES,
        "indicators": INDICATORS,
        "max_retries": MAX_RETRIES,
        "backoff_base": BACKOFF_BASE,
        "timeout": TIMEOUT,
        "raw_dir": PROJECT_ROOT / "data" / "raw",
    }
    if yaml is None or not Path(path).exists():
        logger.warning("Config not loaded (%s); using built-in defaults", path)
        return apply_env_overrides(settings)
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    api = cfg.get("api", {})
    retry = api.get("retry", {})
    settings.update({
        "base_url": api.get("base_url", BASE_URL),
        "countries": api.get("countries", COUNTRIES),
        "indicators": api.get("indicators", INDICATORS),
        "max_retries": retry.get("max_retries", MAX_RETRIES),
        "backoff_base": retry.get("backoff_base_seconds", BACKOFF_BASE),
        "timeout": api.get("timeout_seconds", TIMEOUT),
        "raw_dir": PROJECT_ROOT / cfg.get("paths", {}).get("raw_data_dir", "data/raw"),
    })
    logger.info("Loaded configuration from %s", path)
    return apply_env_overrides(settings)


def apply_env_overrides(settings):
    """Override settings from environment variables (used by docker-compose).

    Supported: WORLD_BANK_API_BASE_URL, API_TIMEOUT_SECONDS, API_MAX_RETRIES, RAW_DATA_DIR.

    Args:
        settings: Settings dict from :func:`load_config`.

    Returns:
        The updated settings dict.
    """
    if os.getenv("WORLD_BANK_API_BASE_URL"):
        settings["base_url"] = os.environ["WORLD_BANK_API_BASE_URL"]
    if os.getenv("API_TIMEOUT_SECONDS"):
        settings["timeout"] = int(os.environ["API_TIMEOUT_SECONDS"])
    if os.getenv("API_MAX_RETRIES"):
        settings["max_retries"] = int(os.environ["API_MAX_RETRIES"])
    if os.getenv("RAW_DATA_DIR"):
        settings["raw_dir"] = Path(os.environ["RAW_DATA_DIR"])
    return settings


def build_url(base_url, countries, indicator_code, mrv):
    """Build a World Bank Indicators API URL.

    Args:
        base_url: API root, e.g. ``https://api.worldbank.org/v2``.
        countries: Iterable of ISO2/ISO3 country codes.
        indicator_code: World Bank indicator id (e.g. ``LP.LPI.OVRL.XQ``).
        mrv: Number of most recent values to request.

    Returns:
        Fully-qualified request URL as a string.
    """
    country_str = ";".join(countries)
    return (f"{base_url}/country/{country_str}/indicator/{indicator_code}"
            f"?format=json&per_page=200&mrv={mrv}")


def fetch_json(url, max_retries=MAX_RETRIES, backoff_base=BACKOFF_BASE, timeout=TIMEOUT):
    """GET a URL and parse JSON, retrying with exponential backoff.

    Retries on connection errors, timeouts, retryable HTTP status codes and
    malformed JSON. Waits ``backoff_base ** attempt`` seconds between tries.

    Args:
        url: Request URL.
        max_retries: Number of retries after the first attempt.
        backoff_base: Base for exponential backoff in seconds.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed JSON payload (list for World Bank responses).

    Raises:
        RuntimeError: If all attempts fail or a non-retryable HTTP error occurs.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            logger.debug("GET %s (attempt %d)", url, attempt + 1)
            resp = requests.get(url, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"Retryable HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            last_error = exc
            if status is not None and status not in RETRY_STATUS:
                logger.error("Non-retryable HTTP error %s for %s", status, url)
                raise RuntimeError(f"HTTP {status} for {url}") from exc
            logger.warning("HTTP error on attempt %d/%d: %s", attempt + 1, max_retries + 1, exc)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = exc
            logger.warning("Network error on attempt %d/%d: %s", attempt + 1, max_retries + 1, exc)
        except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
            last_error = exc
            logger.warning("JSON parse error on attempt %d/%d: %s", attempt + 1, max_retries + 1, exc)
        if attempt < max_retries:
            wait = backoff_base ** (attempt + 1)
            logger.info("Retrying in %s seconds...", wait)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {max_retries + 1} attempts: {last_error}")


def parse_records(payload, indicator_name):
    """Convert a World Bank JSON payload into tidy row dictionaries.

    Null observations are dropped. Some legacy indicators (e.g. road
    density) return an empty ``countryiso3code``; in that case the
    ``country.id`` field is used instead.

    Args:
        payload: Parsed JSON list ``[metadata, observations]``.
        indicator_name: Human-readable indicator label for the CSV.

    Returns:
        List of dicts with keys country_code, country_name, indicator, year, value.
    """
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        message = payload[0].get("message") if isinstance(payload, list) and payload else payload
        logger.warning("Empty or error payload for %s: %s", indicator_name, message)
        return []
    rows = []
    for obs in payload[1]:
        if obs.get("value") is None:
            continue
        country = obs.get("country", {})
        rows.append({
            "country_code": obs.get("countryiso3code") or country.get("id", ""),
            "country_name": country.get("value", ""),
            "indicator": indicator_name,
            "year": obs.get("date"),
            "value": obs.get("value"),
        })
    return rows


def save_outputs(raw_dir, key, payload, rows):
    """Persist the raw JSON payload and tidy CSV for a single indicator.

    Args:
        raw_dir: Destination directory (created if missing).
        key: File stem, e.g. ``lpi_overall``.
        payload: Raw JSON payload.
        rows: Parsed rows from :func:`parse_records`.

    Returns:
        Tuple of (json_path, csv_path).
    """
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_path = raw_dir / f"{key}.json"
    csv_path = raw_dir / f"{key}.csv"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["country_code", "country_name", "indicator", "year", "value"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Saved %s (%d rows) and %s", csv_path.name, len(rows), json_path.name)
    return json_path, csv_path


def ingest(dry_run=False):
    """Run ingestion for every configured indicator.

    Args:
        dry_run: If True, only validate configuration and log request URLs.

    Returns:
        dict mapping indicator key -> number of rows written (0 in dry-run).
    """
    cfg = load_config()
    summary = {}
    logger.info("Starting ingestion for %d indicators x %d countries (dry_run=%s)",
                len(cfg["indicators"]), len(cfg["countries"]), dry_run)
    for key, meta in cfg["indicators"].items():
        url = build_url(cfg["base_url"], cfg["countries"], meta["code"], meta.get("mrv", 5))
        if dry_run:
            logger.info("[DRY-RUN] %s -> %s", key, url)
            summary[key] = 0
            continue
        try:
            payload = fetch_json(url, cfg["max_retries"], cfg["backoff_base"], cfg["timeout"])
        except RuntimeError as exc:
            logger.error("Skipping %s: %s", key, exc)
            summary[key] = -1
            continue
        rows = parse_records(payload, meta["name"])
        save_outputs(cfg["raw_dir"], key, payload, rows)
        summary[key] = len(rows)
    logger.info("Ingestion finished: %s", summary)
    return summary


def main(argv=None):
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Fetch World Bank road & trade indicators.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate configuration and print URLs without downloading.")
    args = parser.parse_args(argv)
    summary = ingest(dry_run=args.dry_run)
    failures = [k for k, v in summary.items() if v < 0]
    if failures:
        logger.error("Failed indicators: %s", failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
