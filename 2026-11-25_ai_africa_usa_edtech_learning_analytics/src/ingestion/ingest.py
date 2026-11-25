"""Ingestion module for the EdTech & Learning Analytics pipeline.

Fetches five education / digital-access indicators for 15 African
countries and the USA from the World Bank Open Data API
(https://api.worldbank.org/v2/) and writes one tidy CSV per indicator
to ``data/raw/`` with the columns::

    country_id, country_name, indicator_id, indicator_name, year, value

Requests use a timeout and are retried up to 3 times with exponential
backoff. Run directly with ``python src/ingestion/ingest.py``.
"""

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
CSV_COLUMNS = [
    "country_id", "country_name", "indicator_id",
    "indicator_name", "year", "value",
]

logger = logging.getLogger("ingest")


def load_config(path=None):
    """Load the YAML configuration file.

    Args:
        path: Optional path to the config file. Falls back to the
            ``CONFIG_PATH`` environment variable, then the default.

    Returns:
        dict: Parsed configuration.
    """
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(config):
    """Configure root logging from the ``logging`` config section.

    Args:
        config: Full configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    level = os.environ.get("LOG_LEVEL", log_cfg.get("level", "INFO"))
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
    )


def fetch_with_retry(url, params, timeout=30, retries=3, backoff=2.0):
    """GET a URL and return decoded JSON, retrying on failure.

    Waits ``backoff ** attempt`` seconds between attempts
    (exponential backoff: 1s, 2s, 4s ... for backoff=2).

    Args:
        url: Endpoint URL.
        params: Query-string parameters.
        timeout: Per-request timeout in seconds.
        retries: Maximum number of attempts.
        backoff: Base of the exponential backoff.

    Returns:
        The decoded JSON payload.

    Raises:
        RuntimeError: If every attempt fails.
    """
    last_error = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = backoff ** attempt
            logger.warning(
                "Attempt %d/%d failed for %s: %s", attempt + 1, retries,
                url, exc,
            )
            if attempt < retries - 1:
                logger.info("Retrying in %.1f seconds", wait)
                time.sleep(wait)
    raise RuntimeError(f"All {retries} attempts failed for {url}: {last_error}")


def fetch_indicator(api_cfg, indicator_code, country_codes):
    """Download every page of one indicator for a list of countries.

    Args:
        api_cfg: The ``world_bank_api`` config section.
        indicator_code: World Bank indicator id, e.g. ``IT.NET.USER.ZS``.
        country_codes: List of ISO-2 country codes.

    Returns:
        list[dict]: Rows with the standard CSV columns (nulls dropped).
    """
    url = (
        f"{api_cfg['base_url'].rstrip('/')}/country/"
        f"{';'.join(country_codes)}/indicator/{indicator_code}"
    )
    rows = []
    page, pages = 1, 1
    while page <= pages:
        params = {
            "format": api_cfg.get("format", "json"),
            "per_page": api_cfg.get("per_page", 500),
            "date": api_cfg.get("date_range", "2015:2025"),
            "page": page,
        }
        payload = fetch_with_retry(
            url, params,
            timeout=api_cfg.get("timeout", 30),
            retries=api_cfg.get("retries", 3),
            backoff=api_cfg.get("backoff_factor", 2),
        )
        if not isinstance(payload, list) or len(payload) < 2:
            message = payload[0] if isinstance(payload, list) else payload
            raise RuntimeError(f"Unexpected API response: {message}")
        meta, records = payload[0], payload[1] or []
        pages = int(meta.get("pages", 1))
        for rec in records:
            if rec.get("value") is None:
                continue
            rows.append({
                "country_id": rec["country"]["id"],
                "country_name": rec["country"]["value"],
                "indicator_id": rec["indicator"]["id"],
                "indicator_name": rec["indicator"]["value"],
                "year": int(rec["date"]),
                "value": float(rec["value"]),
            })
        page += 1
    logger.info("Fetched %d non-null rows for %s", len(rows), indicator_code)
    return rows


def save_rows(rows, path):
    """Write rows to CSV with the standard column order.

    Args:
        rows: List of row dictionaries.
        path: Destination file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Saved %d rows to %s", len(rows), path)


def ingest(config=None, dry_run=False):
    """Run ingestion for all configured indicators.

    Args:
        config: Configuration dict (loaded from disk if omitted).
        dry_run: If True, only log what would be fetched.

    Returns:
        dict: Mapping of indicator code to number of rows saved.
    """
    config = config or load_config()
    api_cfg = config["world_bank_api"]
    raw_dir = PROJECT_ROOT / config["paths"]["raw_data_dir"]
    countries = [c["code"] for c in api_cfg["countries"]]
    summary = {}
    failures = []
    for indicator in api_cfg["indicators"]:
        code, out_file = indicator["code"], raw_dir / indicator["file"]
        if dry_run:
            logger.info("[dry-run] would fetch %s -> %s", code, out_file)
            summary[code] = 0
            continue
        try:
            rows = fetch_indicator(api_cfg, code, countries)
            save_rows(rows, out_file)
            summary[code] = len(rows)
        except RuntimeError as exc:
            logger.error("Failed to ingest %s: %s", code, exc)
            failures.append(code)
    if failures:
        raise RuntimeError(f"Ingestion failed for indicators: {failures}")
    return summary


def main(argv=None):
    """Command-line entry point.

    Args:
        argv: Optional list of CLI arguments.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config without calling the API")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    setup_logging(config)
    try:
        summary = ingest(config, dry_run=args.dry_run)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("Ingestion complete: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
