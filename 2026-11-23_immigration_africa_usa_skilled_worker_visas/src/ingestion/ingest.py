"""Ingestion stage of the Africa-USA skilled worker migration pipeline.

Fetches six World Bank development indicators for ten African source
countries (Egypt, Nigeria, Kenya, Ghana, Ethiopia, Morocco, Tanzania, Uganda,
Senegal, South Africa) plus net migration for the United States, then writes:

* one raw JSON file per indicator (the World Bank ``[metadata, records]``
  payload, unchanged) in ``data/raw/``;
* a combined long-to-wide CSV ``data/raw/africa_migration_indicators.csv``
  with one row per country-year.

HTTP calls use ``tenacity`` for 3 attempts with exponential backoff. Run
``python src/ingestion/ingest.py --dry-run`` to validate the configuration
and print the request URLs without touching the network.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
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
CSV_COLUMNS = [
    "country",
    "iso3",
    "year",
    "net_migration",
    "remittances_pct_gdp",
    "remittances_usd",
    "gdp_per_capita",
    "tertiary_enrollment",
    "population",
]

logger = logging.getLogger("ingestion")


class WorldBankAPIError(RuntimeError):
    """Raised when the World Bank API returns an error payload."""


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var takes priority)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(config: dict[str, Any]) -> None:
    """Configure root logging from config, honouring the LOG_LEVEL env var."""
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
        datefmt=log_cfg.get("datefmt"),
    )


def resolve(path: str) -> Path:
    """Resolve a config path relative to the project root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def build_url(base_url: str, countries: list[str], indicator: str) -> str:
    """Build a World Bank v2 indicator URL for one or more ISO3 codes."""
    return f"{base_url.rstrip('/')}/country/{';'.join(countries)}/indicator/{indicator}"


def make_fetcher(api_cfg: dict[str, Any]):
    """Create an HTTP fetch function wrapped with tenacity retry logic."""
    session = requests.Session()
    session.headers.update({"User-Agent": "africa-usa-migration-pipeline/1.0"})
    timeout = api_cfg.get("timeout_seconds", 30)

    @retry(
        reraise=True,
        stop=stop_after_attempt(api_cfg.get("max_retries", 3)),
        wait=wait_exponential(
            multiplier=api_cfg.get("backoff_multiplier", 2),
            max=api_cfg.get("backoff_max_seconds", 30),
        ),
        retry=retry_if_exception_type(
            (requests.ConnectionError, requests.Timeout, requests.HTTPError)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def fetch(url: str, params: dict[str, Any]) -> list[Any]:
        logger.debug("GET %s params=%s", url, params)
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or len(payload) < 2:
            message = payload[0].get("message") if isinstance(payload, list) and payload else payload
            raise WorldBankAPIError(f"Unexpected World Bank response for {url}: {message}")
        return payload

    return fetch


def fetch_indicator(fetch, base_url: str, countries: list[str], code: str,
                    start_year: int, end_year: int, per_page: int) -> list[Any]:
    """Fetch every page of an indicator and return a single ``[meta, records]`` payload."""
    url = build_url(base_url, countries, code)
    params = {"format": "json", "date": f"{start_year}:{end_year}", "per_page": per_page, "page": 1}
    payload = fetch(url, params)
    meta, records = payload[0], list(payload[1] or [])
    for page in range(2, int(meta.get("pages", 1)) + 1):
        params["page"] = page
        records.extend(fetch(url, params)[1] or [])
    meta = {**meta, "page": 1, "pages": 1, "per_page": len(records), "total": len(records)}
    logger.info("Fetched %s: %d records", code, len(records))
    return [meta, records]


def records_to_frame(records: list[dict[str, Any]], column: str) -> pd.DataFrame:
    """Convert World Bank records into a tidy ``country, iso3, year, <column>`` frame."""
    rows = [
        {
            "country": rec["country"]["value"],
            "iso3": rec["countryiso3code"],
            "year": int(rec["date"]),
            column: rec["value"],
        }
        for rec in records
        if rec.get("countryiso3code")
    ]
    return pd.DataFrame(rows, columns=["country", "iso3", "year", column])


def combine_indicators(frames: dict[str, pd.DataFrame], countries: list[str],
                       start_year: int, end_year: int) -> pd.DataFrame:
    """Merge per-indicator frames onto a complete country x year grid."""
    names = pd.concat([f[["iso3", "country"]] for f in frames.values()]).drop_duplicates("iso3")
    grid = pd.MultiIndex.from_product(
        [countries, range(start_year, end_year + 1)], names=["iso3", "year"]
    ).to_frame(index=False)
    combined = grid.merge(names, on="iso3", how="left")
    for column, frame in frames.items():
        combined = combined.merge(frame[["iso3", "year", column]], on=["iso3", "year"], how="left")
    combined["country"] = combined["country"].fillna(combined["iso3"])
    combined["iso3"] = pd.Categorical(combined["iso3"], categories=countries, ordered=True)
    combined = combined.sort_values(["iso3", "year"]).reset_index(drop=True)
    combined["iso3"] = combined["iso3"].astype(str)
    return combined[CSV_COLUMNS]


def write_json(payload: list[Any], path: Path) -> None:
    """Write a payload to disk atomically (temp file + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    tmp.replace(path)


def ingest(config: dict[str, Any], output_dir: Path | None = None, dry_run: bool = False) -> Path | None:
    """Run the ingestion stage; returns the combined CSV path (``None`` on dry run)."""
    api = config["world_bank_api"]
    pipe = config["pipeline"]
    countries = [c["iso3"] for c in pipe["countries"]]
    start, end = int(pipe["start_year"]), int(pipe["end_year"])
    raw_dir = Path(output_dir) if output_dir else resolve(config["paths"]["raw_dir"])
    combined_csv = raw_dir / Path(config["paths"]["combined_raw_csv"]).name

    if start > end:
        raise ValueError(f"start_year {start} is after end_year {end}")
    if not countries:
        raise ValueError("No countries configured under pipeline.countries")

    if dry_run:
        logger.info("Dry run: %d countries, years %d-%d, output %s", len(countries), start, end, raw_dir)
        for name, ind in api["indicators"].items():
            logger.info("  %-20s %s", name, build_url(api["base_url"], countries, ind["code"]))
        usa = api["usa_indicator"]
        logger.info("  %-20s %s", "usa_net_migration", build_url(api["base_url"], [usa["country"]], usa["code"]))
        return None

    raw_dir.mkdir(parents=True, exist_ok=True)
    fetch = make_fetcher(api)
    per_page = int(api.get("per_page", 1000))
    frames: dict[str, pd.DataFrame] = {}
    failures: list[str] = []

    for name, ind in api["indicators"].items():
        try:
            payload = fetch_indicator(fetch, api["base_url"], countries, ind["code"], start, end, per_page)
        except (requests.RequestException, WorldBankAPIError, ValueError) as exc:
            logger.error("Failed to fetch %s (%s): %s", name, ind["code"], exc)
            failures.append(name)
            continue
        write_json(payload, raw_dir / ind["raw_file"])
        frames[name] = records_to_frame(payload[1], name)

    usa = api["usa_indicator"]
    try:
        usa_payload = fetch_indicator(fetch, api["base_url"], [usa["country"]], usa["code"], start, end, per_page)
        write_json(usa_payload, raw_dir / usa["raw_file"])
    except (requests.RequestException, WorldBankAPIError, ValueError) as exc:
        logger.error("Failed to fetch USA net migration: %s", exc)
        failures.append("usa_net_migration")

    if not frames:
        raise RuntimeError("All World Bank indicator requests failed; nothing written")

    for name in api["indicators"]:
        frames.setdefault(name, pd.DataFrame(columns=["country", "iso3", "year", name]))
    combined = combine_indicators(frames, countries, start, end)
    combined.to_csv(combined_csv, index=False)
    logger.info("Wrote %s (%d rows x %d cols)", combined_csv, *combined.shape)
    if failures:
        logger.warning("Completed with failed indicators: %s", ", ".join(failures))
    return combined_csv


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--output-dir", help="Override the raw output directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and print URLs only")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(config)
    try:
        ingest(config, Path(args.output_dir) if args.output_dir else None, args.dry_run)
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
