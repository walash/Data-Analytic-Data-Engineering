"""Ingestion stage of the Africa LNG Exports to the USA pipeline.

Fetches two public datasets and writes them as tidy CSVs under ``data/raw/``:

1. **UN Comtrade** (public preview API, no key required) - annual trade in
   HS 271111 (liquefied natural gas) reported by ten African countries for
   2019-2023, all partners, flows X (export), M (import) and RX (re-export).
   Output: ``data/raw/lng_trade_comtrade.csv`` with columns
   ``year, reporter_code, reporter, partner_code, partner, flow,
   trade_value_usd, qty_kg, commodity``.
2. **World Bank Open Data API** - seven energy / macro indicators (gas, oil and
   total resource rents, merchandise exports, electricity from gas, GDP per
   capita, population) for the same countries.
   Output: ``data/raw/worldbank_energy_indicators.csv`` with columns
   ``country_code, country, year, indicator, indicator_name, value``.

HTTP calls are wrapped in a ``tenacity`` retry policy with exponential backoff.
If a source cannot be reached and a previously downloaded raw file exists, the
existing file is kept (configurable via ``pipeline.fallback_to_existing_raw``)
so downstream stages remain runnable offline.

Usage::

    python src/ingestion/ingest.py            # download everything
    python src/ingestion/ingest.py --dry-run  # validate config, no network
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

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

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("ingest")

COMTRADE_COLUMNS = [
    "year", "reporter_code", "reporter", "partner_code", "partner",
    "flow", "trade_value_usd", "qty_kg", "commodity",
]
WORLDBANK_COLUMNS = [
    "country_code", "country", "year", "indicator", "indicator_name", "value",
]


class TransientAPIError(Exception):
    """Raised for retryable HTTP conditions (429 / 5xx)."""


def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var wins)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    logger.debug("Loaded configuration from %s", config_path)
    return config


def raw_dir(config: Dict[str, Any]) -> Path:
    """Return (and create) the absolute raw-data directory."""
    path = PROJECT_ROOT / config["paths"]["raw_data_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_session() -> requests.Session:
    """Create an HTTP session with a descriptive User-Agent."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "africa-lng-pipeline/1.0 (data engineering portfolio)",
        "Accept": "application/json",
    })
    return session


def get_json(session: requests.Session, url: str, params: Dict[str, Any],
             timeout: int, max_retries: int, backoff: float) -> Any:
    """GET ``url`` and decode JSON, retrying on network errors, 429 and 5xx."""

    @retry(
        reraise=True,
        stop=stop_after_attempt(max_retries),
        wait=wait_exponential(multiplier=backoff, min=backoff, max=60),
        retry=retry_if_exception_type(
            (TransientAPIError, requests.ConnectionError, requests.Timeout)
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _call() -> Any:
        response = session.get(url, params=params, timeout=timeout)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientAPIError(
                f"HTTP {response.status_code} from {response.url}"
            )
        response.raise_for_status()
        return response.json()

    return _call()


# --------------------------------------------------------------------------- #
# UN Comtrade
# --------------------------------------------------------------------------- #
def parse_comtrade_records(records: List[Dict[str, Any]],
                           reporter_names: Dict[int, str],
                           commodity_label: str) -> pd.DataFrame:
    """Convert Comtrade ``data`` records into the canonical raw schema.

    The public preview endpoint frequently returns ``null`` descriptions, so
    reporter names come from the config and missing partner names fall back
    to the numeric partner code (resolved later in the transformation stage).
    """
    rows = []
    for rec in records:
        reporter_code = int(rec.get("reporterCode") or 0)
        partner_code = int(rec.get("partnerCode") or 0)
        partner_desc = rec.get("partnerDesc")
        if not partner_desc:
            partner_desc = "World" if partner_code == 0 else str(partner_code)
        qty = rec.get("netWgt")
        if qty in (None, 0):
            qty = rec.get("qty")
        rows.append({
            "year": int(rec.get("refYear") or rec.get("period")),
            "reporter_code": reporter_code,
            "reporter": rec.get("reporterDesc")
            or reporter_names.get(reporter_code, str(reporter_code)),
            "partner_code": partner_code,
            "partner": partner_desc,
            "flow": rec.get("flowCode"),
            "trade_value_usd": rec.get("primaryValue"),
            "qty_kg": qty,
            "commodity": commodity_label,
        })
    return pd.DataFrame(rows, columns=COMTRADE_COLUMNS)


def fetch_comtrade(config: Dict[str, Any],
                   session: requests.Session) -> pd.DataFrame:
    """Download HS 271111 trade for every configured reporter and year.

    The public preview endpoint accepts a single period per request (and
    rate-limits bursts), so calls are issued per reporter-year with a pause.
    """
    api = config["api"]
    ct = api["comtrade"]
    reporter_names = {c["m49"]: c["name"] for c in config["countries"]}
    frames: List[pd.DataFrame] = []
    for country in config["countries"]:
        for year in config["years"]:
            params = {
                "reporterCode": country["m49"],
                "period": year,
                "cmdCode": ct["hs_code"],
                "flowCode": ",".join(ct["flow_codes"]),
                "includeDesc": "true",
            }
            try:
                payload = get_json(session, ct["base_url"], params,
                                   api["timeout_seconds"], api["max_retries"],
                                   api["backoff_seconds"])
            except (requests.RequestException, TransientAPIError,
                    ValueError) as exc:
                logger.error("Comtrade request failed for %s %s: %s",
                             country["name"], year, exc)
                time.sleep(api["pause_between_requests_seconds"])
                continue
            records = (payload.get("data") or []) \
                if isinstance(payload, dict) else []
            frame = parse_comtrade_records(records, reporter_names,
                                           ct["commodity_label"])
            logger.info("Comtrade %-18s %s -> %4d rows",
                        country["name"], year, len(frame))
            if not frame.empty:
                frames.append(frame)
            time.sleep(api["pause_between_requests_seconds"])
    if not frames:
        return pd.DataFrame(columns=COMTRADE_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(
        subset=["year", "reporter_code", "partner_code", "flow"]
    )
    return result.sort_values(["year", "reporter", "flow", "partner_code"])


# --------------------------------------------------------------------------- #
# World Bank
# --------------------------------------------------------------------------- #
def parse_worldbank_payload(payload: Any, indicator: str,
                            indicator_name: str) -> pd.DataFrame:
    """Flatten a World Bank ``[metadata, records]`` response to rows."""
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return pd.DataFrame(columns=WORLDBANK_COLUMNS)
    rows = []
    for rec in payload[1]:
        if rec.get("value") is None:
            continue
        rows.append({
            "country_code": rec.get("countryiso3code")
            or rec.get("country", {}).get("id"),
            "country": rec.get("country", {}).get("value"),
            "year": int(rec["date"]),
            "indicator": indicator,
            "indicator_name": indicator_name,
            "value": float(rec["value"]),
        })
    return pd.DataFrame(rows, columns=WORLDBANK_COLUMNS)


def fetch_worldbank(config: Dict[str, Any],
                    session: requests.Session) -> pd.DataFrame:
    """Download every configured indicator for all countries (paginated)."""
    api = config["api"]
    wb = api["worldbank"]
    countries = ";".join(c["iso3"] for c in config["countries"])
    frames: List[pd.DataFrame] = []
    for indicator, name in config["indicators"].items():
        url = f"{wb['base_url']}/country/{countries}/indicator/{indicator}"
        page, pages = 1, 1
        while page <= pages:
            params = {"format": wb["format"], "per_page": wb["per_page"],
                      "date": wb["date_range"], "page": page}
            try:
                payload = get_json(session, url, params,
                                   api["timeout_seconds"], api["max_retries"],
                                   api["backoff_seconds"])
            except (requests.RequestException, TransientAPIError,
                    ValueError) as exc:
                logger.error("World Bank request failed for %s: %s",
                             indicator, exc)
                break
            if isinstance(payload, list) and payload and \
                    isinstance(payload[0], dict):
                pages = int(payload[0].get("pages") or 1)
            frame = parse_worldbank_payload(payload, indicator, name)
            if not frame.empty:
                frames.append(frame)
            page += 1
        logger.info("World Bank %-20s fetched", indicator)
        time.sleep(api["pause_between_requests_seconds"])
    if not frames:
        return pd.DataFrame(columns=WORLDBANK_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset=["country_code", "year", "indicator"])
    return result.sort_values(["indicator", "country_code", "year"],
                              ascending=[True, True, False])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def save_or_fallback(frame: pd.DataFrame, target: Path,
                     allow_fallback: bool, label: str) -> Path:
    """Write ``frame`` to ``target``; keep an existing file if ``frame`` is empty."""
    if frame.empty:
        if allow_fallback and target.exists():
            logger.warning("%s: no fresh data, keeping existing %s",
                           label, target.name)
            return target
        raise RuntimeError(f"{label}: no data downloaded and no fallback file")
    tmp = target.with_suffix(".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(target)  # atomic swap avoids half-written files
    logger.info("%s: wrote %d rows to %s", label, len(frame), target)
    return target


def ingest(config: Dict[str, Any] | None = None,
           dry_run: bool = False) -> Dict[str, Path]:
    """Run the full ingestion stage and return the written file paths."""
    config = config or load_config()
    out_dir = raw_dir(config)
    targets = {
        "comtrade": out_dir / config["paths"]["comtrade_file"],
        "worldbank": out_dir / config["paths"]["worldbank_file"],
    }
    if dry_run:
        logger.info("Dry run: %d countries, %d indicators, years %s",
                    len(config["countries"]), len(config["indicators"]),
                    config["years"])
        for name, path in targets.items():
            logger.info("Dry run: %s -> %s (exists=%s)", name, path,
                        path.exists())
        return targets

    allow_fallback = bool(config["pipeline"].get("fallback_to_existing_raw"))
    session = build_session()
    try:
        trade = fetch_comtrade(config, session)
        save_or_fallback(trade, targets["comtrade"], allow_fallback,
                         "UN Comtrade")
        indicators = fetch_worldbank(config, session)
        save_or_fallback(indicators, targets["worldbank"], allow_fallback,
                         "World Bank")
    finally:
        session.close()
    return targets


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="validate configuration without network calls")
    parser.add_argument("--config", default=None, help="path to config.yaml")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    args = parse_args(argv)
    try:
        ingest(load_config(args.config), dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the CLI
        logger.exception("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
