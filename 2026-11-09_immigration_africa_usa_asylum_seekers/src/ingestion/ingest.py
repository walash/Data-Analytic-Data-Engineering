"""Ingestion stage for the African Asylum Seekers & Immigration Flows to the USA pipeline.

Fetches four World Bank Open Data indicators for the 53 African countries in
``config/config.yaml`` plus the United States:

* SM.POP.NETM           - Net migration
* SM.POP.TOTL           - International migrant stock, total
* SM.POP.TOTL.ZS        - International migrant stock (% of population)
* BX.TRF.PWKR.DT.GD.ZS  - Personal remittances, received (% of GDP)

Outputs (written to ``data/raw/`` by default):

* ``wb_<indicator>.csv``              - one tidy long-format file per indicator
* ``africa_usa_migration_data.csv``   - combined migration table (Africa + USA)
* ``africa_remittances_migration.csv``- remittances joined with migrant stock (Africa)

Usage::

    python src/ingestion/ingest.py              # live download
    python src/ingestion/ingest.py --dry-run    # validate config + URLs only (CI)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

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

logger = logging.getLogger("ingest")


class IngestionError(RuntimeError):
    """Raised when an indicator cannot be downloaded or parsed."""


def load_config(path: Path | None = None) -> dict:
    """Load the YAML configuration, honouring the ``CONFIG_PATH`` env var."""
    cfg_path = Path(path or os.getenv("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    with open(cfg_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def resolve_raw_dir(config: dict, override: str | None = None) -> Path:
    """Return the raw-data directory (CLI override > env var > config)."""
    raw = override or os.getenv("RAW_DATA_DIR") or config["paths"]["raw_data_dir"]
    raw_path = Path(raw)
    if not raw_path.is_absolute():
        raw_path = PROJECT_ROOT / raw_path
    return raw_path


def build_indicator_url(config: dict, indicator_code: str) -> str:
    """Build the World Bank API URL for one indicator across Africa + USA."""
    api = config["api"]
    pipeline = config["pipeline"]
    countries = list(pipeline["african_country_iso_codes"]) + [pipeline["usa_iso_code"]]
    return (
        f"{api['worldbank_base_url']}/country/{';'.join(countries)}"
        f"/indicator/{indicator_code}"
    )


def make_session() -> requests.Session:
    """Create an HTTP session with a descriptive User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": "africa-usa-asylum-pipeline/1.0"})
    return session


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout, requests.HTTPError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _get_page(session: requests.Session, url: str, params: dict, timeout: int) -> list:
    """GET one page of a World Bank response; retried with exponential backoff."""
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        message = payload[0].get("message") if isinstance(payload, list) and payload else payload
        raise IngestionError(f"Unexpected World Bank payload for {url}: {message}")
    return payload


def fetch_indicator(session: requests.Session, config: dict, indicator: dict) -> pd.DataFrame:
    """Download every page of an indicator and return a tidy DataFrame.

    Columns: ``iso3, country_name, year, <indicator name>``.
    """
    api = config["api"]
    url = build_indicator_url(config, indicator["code"])
    params = {"format": api["format"], "per_page": api["per_page"], "mrv": api["mrv"], "page": 1}
    records: List[dict] = []

    while True:
        meta, rows = _get_page(session, url, params, api["request_timeout"])
        for row in rows:
            records.append(
                {
                    "iso3": row.get("countryiso3code"),
                    "country_name": (row.get("country") or {}).get("value"),
                    "year": int(row["date"]),
                    indicator["name"]: row.get("value"),
                }
            )
        pages = int(meta.get("pages", 1))
        logger.info("%s: page %s/%s (%s rows)", indicator["code"], params["page"], pages, len(rows))
        if params["page"] >= pages:
            break
        params["page"] += 1

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise IngestionError(f"No rows returned for indicator {indicator['code']}")
    years = config["pipeline"]["years_range"]
    df = df[df["year"].between(years["start"], years["end"])]
    return df.drop_duplicates(subset=["iso3", "year"]).reset_index(drop=True)


def combine_outputs(frames: Dict[str, pd.DataFrame], config: dict) -> Dict[str, pd.DataFrame]:
    """Build the two combined raw tables consumed by the transformation stage."""
    usa = config["pipeline"]["usa_iso_code"]
    keys = ["iso3", "country_name", "year"]

    migration = frames["net_migration"]
    for name in ("migrant_stock_total", "migrant_stock_pct_pop"):
        migration = migration.merge(frames[name][["iso3", "year", name]], on=["iso3", "year"], how="left")
    migration["region"] = migration["iso3"].map(lambda c: "USA" if c == usa else "Africa")
    migration = migration[["country_name", "iso3", "year", "net_migration",
                           "migrant_stock_total", "migrant_stock_pct_pop", "region"]]
    migration = migration.sort_values(["region", "country_name", "year"], ascending=[True, True, False])

    remit = frames["remittances_pct_gdp"][keys + ["remittances_pct_gdp"]]
    for name in ("migrant_stock_total", "migrant_stock_pct_pop"):
        remit = remit.merge(frames[name][["iso3", "year", name]], on=["iso3", "year"], how="left")
    remit = remit[remit["iso3"] != usa].sort_values(["country_name", "year"], ascending=[True, False])

    return {
        config["paths"]["migration_raw_file"]: migration.reset_index(drop=True),
        config["paths"]["remittances_raw_file"]: remit.reset_index(drop=True),
    }


def ingest(dry_run: bool = False, output_dir: str | None = None) -> List[Path]:
    """Run the ingestion stage and return the list of files written."""
    config = load_config()
    raw_dir = resolve_raw_dir(config, output_dir)
    indicators = config["api"]["indicators"]
    logger.info("Raw data directory: %s", raw_dir)
    logger.info("Countries requested: %d African + USA", len(config["pipeline"]["african_country_iso_codes"]))

    if dry_run:
        for indicator in indicators:
            logger.info("[dry-run] would fetch %s -> %s", indicator["code"],
                        build_indicator_url(config, indicator["code"]))
        logger.info("[dry-run] configuration valid; no files downloaded.")
        return []

    raw_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    frames: Dict[str, pd.DataFrame] = {}
    written: List[Path] = []

    for indicator in indicators:
        try:
            df = fetch_indicator(session, config, indicator)
        except (requests.RequestException, IngestionError, ValueError) as exc:
            logger.error("Failed to fetch %s: %s", indicator["code"], exc)
            raise IngestionError(f"Ingestion failed for {indicator['code']}") from exc
        out = raw_dir / f"wb_{indicator['code'].replace('.', '_')}.csv"
        df.to_csv(out, index=False)
        frames[indicator["name"]] = df
        written.append(out)
        logger.info("Saved %s (%d rows)", out.name, len(df))

    for filename, df in combine_outputs(frames, config).items():
        out = raw_dir / filename
        df.to_csv(out, index=False)
        written.append(out)
        logger.info("Saved %s (%d rows)", out.name, len(df))

    return written


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Ingest World Bank migration data for Africa + USA.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate configuration and print request URLs without downloading.")
    parser.add_argument("--output-dir", default=None,
                        help="Override the raw data directory (defaults to config paths.raw_data_dir).")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    args = parse_args(argv)
    try:
        files = ingest(dry_run=args.dry_run, output_dir=args.output_dir)
    except IngestionError as exc:
        logger.critical("%s", exc)
        return 1
    logger.info("Ingestion complete: %d file(s) written.", len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
