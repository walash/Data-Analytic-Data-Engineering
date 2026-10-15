"""Ingestion stage: download World Bank indicators for African brain-drain analysis.

Fetches eight World Bank Indicators API series (net migration, remittances,
GDP per capita, unemployment, tertiary enrollment, migrant stock, population)
for 15 African source countries and stores the raw API responses in
``data/raw/`` exactly as returned: ``[metadata, [records...]]``.

The World Bank API is free and needs no API key.

Usage:
    python src/ingestion/ingest.py                       # fetch everything
    python src/ingestion/ingest.py --indicators net_migration unemployment
    python src/ingestion/ingest.py --dry-run             # validate config + URLs only
    python src/ingestion/ingest.py --output-dir /tmp/raw # write elsewhere
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("ingest")


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load the YAML config (path from argument, $CONFIG_PATH, or default)."""
    path = Path(config_path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def validate_indicator(name: str, spec: dict[str, Any], countries: list[str]) -> None:
    """Sanity-check an indicator spec so the URL actually matches its metadata."""
    for key in ("code", "file", "url"):
        if key not in spec:
            raise ValueError(f"Indicator '{name}' is missing required key '{key}'")
    parsed = urlparse(spec["url"])
    if parsed.netloc != "api.worldbank.org":
        raise ValueError(f"Indicator '{name}' URL is not a World Bank API URL: {spec['url']}")
    if f"/indicator/{spec['code']}" not in parsed.path:
        raise ValueError(f"Indicator '{name}' URL does not reference code {spec['code']}")
    url_countries = parsed.path.split("/country/")[1].split("/")[0].split(";")
    missing = sorted(set(countries) - set(url_countries))
    if missing:
        raise ValueError(f"Indicator '{name}' URL is missing countries: {missing}")
    query = parse_qs(parsed.query)
    if query.get("format", [""])[0] != "json":
        raise ValueError(f"Indicator '{name}' URL must request format=json")


def fetch_json(session: requests.Session, url: str, timeout: int, max_retries: int,
               backoff: float) -> Any:
    """GET a URL with simple exponential-backoff retries and return parsed JSON."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = backoff * (2 ** (attempt - 1))
            logger.warning("Attempt %d/%d failed for %s: %s (retrying in %.1fs)",
                           attempt, max_retries, url, exc, wait)
            if attempt < max_retries:
                time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts") from last_error


def fetch_indicator(session: requests.Session, url: str, timeout: int, max_retries: int,
                    backoff: float) -> list[Any]:
    """Fetch all pages of a World Bank indicator and return ``[metadata, records]``."""
    payload = fetch_json(session, url, timeout, max_retries, backoff)
    if not isinstance(payload, list) or len(payload) < 2:
        # The API returns [{"message": [...]}] on invalid requests.
        raise RuntimeError(f"Unexpected World Bank response for {url}: {payload}")

    metadata, records = payload[0], payload[1] or []
    pages = int(metadata.get("pages", 1))
    for page in range(2, pages + 1):
        sep = "&" if "?" in url else "?"
        page_payload = fetch_json(session, f"{url}{sep}page={page}", timeout, max_retries,
                                  backoff)
        records.extend(page_payload[1] or [])

    metadata["total_fetched"] = len(records)
    return [metadata, records]


def summarise(records: list[dict[str, Any]]) -> str:
    """Return a short human-readable summary of fetched records."""
    years = sorted({r.get("date") for r in records if r.get("date")})
    countries = {r.get("country", {}).get("id") for r in records}
    nulls = sum(1 for r in records if r.get("value") is None)
    span = f"{years[0]}-{years[-1]}" if years else "n/a"
    return f"{len(records)} rows, {len(countries)} countries, years {span}, {nulls} nulls"


def ingest(config_path: str | Path | None = None, indicators: list[str] | None = None,
           output_dir: str | Path | None = None, dry_run: bool = False) -> dict[str, Path]:
    """Download the configured indicators. Returns a mapping name -> written file."""
    config = load_config(config_path)
    wb_cfg = config["worldbank"]
    run_cfg = config.get("pipeline", {})
    countries = wb_cfg["countries"]
    all_specs: dict[str, dict[str, Any]] = wb_cfg["indicators"]

    selected = indicators or list(all_specs)
    unknown = sorted(set(selected) - set(all_specs))
    if unknown:
        raise ValueError(f"Unknown indicators requested: {unknown}. "
                         f"Available: {sorted(all_specs)}")

    raw_dir = Path(output_dir) if output_dir else PROJECT_ROOT / config["paths"]["raw_dir"]
    if not raw_dir.is_absolute():
        raw_dir = PROJECT_ROOT / raw_dir

    for name in selected:
        validate_indicator(name, all_specs[name], countries)
    logger.info("Validated %d indicator definitions for %d countries",
                len(selected), len(countries))

    if dry_run:
        for name in selected:
            logger.info("[dry-run] %-22s -> %s", name, raw_dir / all_specs[name]["file"])
            logger.info("[dry-run]   GET %s", all_specs[name]["url"])
        logger.info("Dry run complete - no network calls made, no files written.")
        return {name: raw_dir / all_specs[name]["file"] for name in selected}

    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": run_cfg.get("user_agent", "brain-drain-pipeline")})
    timeout = int(run_cfg.get("request_timeout_seconds", 30))
    max_retries = int(run_cfg.get("max_retries", 3))
    backoff = float(run_cfg.get("backoff_seconds", 2))

    written: dict[str, Path] = {}
    for name in selected:
        spec = all_specs[name]
        logger.info("Fetching %s (%s)", name, spec["code"])
        data = fetch_indicator(session, spec["url"], timeout, max_retries, backoff)
        if not data[1]:
            raise RuntimeError(f"World Bank returned no records for {name} ({spec['code']})")
        target = raw_dir / spec["file"]
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(target)  # atomic swap so a failed run never leaves a half-written file
        written[name] = target
        logger.info("Saved %s -> %s (%s)", name, target, summarise(data[1]))

    logger.info("Ingestion complete: %d files written to %s", len(written), raw_dir)
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest World Bank brain-drain indicators")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--indicators", nargs="+", default=None,
                        help="Subset of indicator names from config (default: all)")
    parser.add_argument("--output-dir", default=None, help="Override raw output directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate config and print URLs without downloading")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config.get("pipeline", {}).get("log_level", "INFO")),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        ingest(args.config, args.indicators, args.output_dir, args.dry_run)
    except Exception as exc:  # noqa: BLE001 - surface any failure as non-zero exit
        logger.error("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
