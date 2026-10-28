"""
Ingestion module for the Africa & USA Tuberculosis Burden pipeline.

Fetches four public, keyless datasets and stores them in ``data/raw/``:

* WHO GHO ``MDG_0000000020`` - TB incidence per 100k (with uncertainty bounds)
* World Bank ``SH.TBS.MORT`` - TB death rate per 100k
* World Bank ``SH.TBS.CURE.ZS`` - TB treatment success rate (% of new cases)
* World Bank ``SH.XPD.CHEX.GD.ZS`` - current health expenditure (% of GDP)

The raw API payloads are saved verbatim (World Bank pages are concatenated
into a single ``[metadata, records]`` document) and tidy, region-filtered
CSV extracts are written for the 54 African Union member states and the USA.

Usage::

    python src/ingestion/ingest.py               # live download + CSV extracts
    python src/ingestion/ingest.py --from-cache  # rebuild CSVs from saved raw JSON
    python src/ingestion/ingest.py --dry-run     # validate config/paths, no writes
    python src/ingestion/ingest.py --dry-run --check-connectivity
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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

logger = logging.getLogger("tb_ingest")

# Human-readable names for the African Union member states plus the USA.
# WHO GHO records only carry ISO-3 codes, so names are attached here.
COUNTRY_NAMES: Dict[str, str] = {
    "DZA": "Algeria", "AGO": "Angola", "BEN": "Benin", "BWA": "Botswana",
    "BFA": "Burkina Faso", "BDI": "Burundi", "CPV": "Cabo Verde",
    "CMR": "Cameroon", "CAF": "Central African Republic", "TCD": "Chad",
    "COM": "Comoros", "COG": "Congo", "COD": "DR Congo",
    "CIV": "Cote d'Ivoire", "DJI": "Djibouti", "EGY": "Egypt",
    "GNQ": "Equatorial Guinea", "ERI": "Eritrea", "SWZ": "Eswatini",
    "ETH": "Ethiopia", "GAB": "Gabon", "GMB": "Gambia", "GHA": "Ghana",
    "GIN": "Guinea", "GNB": "Guinea-Bissau", "KEN": "Kenya",
    "LSO": "Lesotho", "LBR": "Liberia", "LBY": "Libya",
    "MDG": "Madagascar", "MWI": "Malawi", "MLI": "Mali",
    "MRT": "Mauritania", "MUS": "Mauritius", "MAR": "Morocco",
    "MOZ": "Mozambique", "NAM": "Namibia", "NER": "Niger",
    "NGA": "Nigeria", "RWA": "Rwanda", "STP": "Sao Tome and Principe",
    "SEN": "Senegal", "SYC": "Seychelles", "SLE": "Sierra Leone",
    "SOM": "Somalia", "ZAF": "South Africa", "SSD": "South Sudan",
    "SDN": "Sudan", "TZA": "Tanzania", "TGO": "Togo", "TUN": "Tunisia",
    "UGA": "Uganda", "ZMB": "Zambia", "ZWE": "Zimbabwe",
    "USA": "United States",
}


class IngestionError(RuntimeError):
    """Raised when a source cannot be fetched or parsed."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path: Path | str | None = None) -> Dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var overrides default)."""
    cfg_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    for key in ("data_sources", "paths", "pipeline", "geography"):
        if key not in config:
            raise ValueError(f"Config missing required section '{key}'")
    return config


def africa_codes(config: Dict[str, Any]) -> Dict[str, str]:
    """Return a mapping ISO-3 code -> African sub-region from the config."""
    mapping: Dict[str, str] = {}
    for sub_region, codes in config["geography"]["africa_codes"].items():
        for code in codes:
            mapping[code] = sub_region
    return mapping


def raw_dir(config: Dict[str, Any]) -> Path:
    """Absolute path of the raw data directory (created if missing)."""
    path = PROJECT_ROOT / config["paths"]["raw_dir"]
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# HTTP layer with retries
# ---------------------------------------------------------------------------
def build_session() -> requests.Session:
    """Create a requests session with an identifying User-Agent."""
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "tb-burden-pipeline/1.0 (data engineering portfolio)",
         "Accept": "application/json",
         # The WHO GHO CDN intermittently returns HTTP 500 for gzip-encoded
         # responses of large indicators; requesting identity encoding avoids it.
         "Accept-Encoding": "identity"}
    )
    return session


def make_fetcher(config: Dict[str, Any], session: requests.Session):
    """Build a JSON fetch function wrapped with tenacity retry/backoff."""
    pipe = config["pipeline"]
    timeout = pipe.get("request_timeout_seconds", 60)

    @retry(
        reraise=True,
        stop=stop_after_attempt(pipe.get("max_retries", 4)),
        wait=wait_exponential(multiplier=pipe.get("backoff_multiplier", 2),
                              max=pipe.get("backoff_max_seconds", 30)),
        retry=retry_if_exception_type((requests.RequestException, ValueError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def fetch_json(url: str) -> Any:
        logger.debug("GET %s", url)
        response = session.get(url, timeout=timeout)
        if response.status_code >= 500 or response.status_code == 429:
            raise requests.HTTPError(
                f"Retryable HTTP {response.status_code} for {url}", response=response)
        response.raise_for_status()
        return response.json()

    return fetch_json


def with_query(url: str, **params: Any) -> str:
    """Return ``url`` with the given query parameters added/overridden."""
    parts = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    query.update({k: str(v) for k, v in params.items()})
    return urlunparse(parts._replace(query=urlencode(query, safe="$ '")))


# ---------------------------------------------------------------------------
# Source-specific fetchers
# ---------------------------------------------------------------------------
def fetch_who_gho(url: str, fetch_json) -> Dict[str, Any]:
    """Fetch a WHO GHO OData indicator, following ``@odata.nextLink`` pages."""
    payload = fetch_json(url)
    if not isinstance(payload, dict) or "value" not in payload:
        raise IngestionError(f"Unexpected WHO GHO payload shape from {url}")
    records: List[Dict[str, Any]] = list(payload["value"])
    next_link = payload.get("@odata.nextLink")
    while next_link:
        page = fetch_json(next_link)
        records.extend(page.get("value", []))
        next_link = page.get("@odata.nextLink")
    payload["value"] = records
    payload.pop("@odata.nextLink", None)
    logger.info("WHO GHO: %d records fetched", len(records))
    return payload


def fetch_world_bank(url: str, fetch_json) -> List[Any]:
    """Fetch every page of a World Bank v2 indicator endpoint.

    Returns a ``[metadata, records]`` list mirroring the API's own layout.
    """
    first = fetch_json(with_query(url, page=1))
    if not isinstance(first, list) or len(first) < 2 or first[1] is None:
        message = first[0].get("message") if isinstance(first, list) and first else first
        raise IngestionError(f"World Bank API error for {url}: {message}")
    meta, records = first[0], list(first[1])
    total_pages = int(meta.get("pages", 1))
    for page_no in range(2, total_pages + 1):
        page = fetch_json(with_query(url, page=page_no))
        if isinstance(page, list) and len(page) > 1 and page[1]:
            records.extend(page[1])
        logger.debug("World Bank page %d/%d fetched", page_no, total_pages)
    meta = dict(meta, page=1, pages=total_pages, per_page=len(records),
                pages_fetched=total_pages)
    logger.info("World Bank %s: %d records over %d page(s)",
                records[0]["indicator"]["id"] if records else "?",
                len(records), total_pages)
    return [meta, records]


def save_json(payload: Any, path: Path) -> None:
    """Write a JSON document atomically (temp file + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    tmp.replace(path)
    logger.info("Saved %s (%.1f KB)", path.name, path.stat().st_size / 1024)


def load_json(path: Path) -> Any:
    """Read a JSON file, raising IngestionError if missing."""
    if not path.exists():
        raise IngestionError(f"Raw file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Tidy CSV extracts
# ---------------------------------------------------------------------------
def extract_who_incidence(payload: Dict[str, Any], codes: Iterable[str]) -> List[Tuple]:
    """Return incidence rows ``(code, name, year, value, low, high)`` for codes."""
    wanted = set(codes)
    rows = []
    for rec in payload.get("value", []):
        code = rec.get("SpatialDim")
        if rec.get("SpatialDimType") != "COUNTRY" or code not in wanted:
            continue
        if rec.get("NumericValue") is None:
            continue
        rows.append((code, COUNTRY_NAMES.get(code, code), int(rec["TimeDim"]),
                     rec["NumericValue"], rec.get("Low"), rec.get("High")))
    return sorted(rows, key=lambda r: (r[0], -r[2]))


def extract_world_bank(payload: List[Any], codes: Iterable[str]) -> List[Tuple]:
    """Return ``(code, name, year, value)`` rows with non-null values for codes."""
    wanted = set(codes)
    rows = []
    for rec in payload[1] if len(payload) > 1 and payload[1] else []:
        code = rec.get("countryiso3code")
        if code not in wanted or rec.get("value") is None:
            continue
        rows.append((code, COUNTRY_NAMES.get(code, rec["country"]["value"]),
                     int(rec["date"]), rec["value"]))
    return sorted(rows, key=lambda r: (r[0], -r[2]))


def write_csv(path: Path, header: List[str], rows: List[Tuple]) -> None:
    """Write rows to CSV with a header line."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    logger.info("Wrote %s (%d rows)", path.name, len(rows))


def write_extracts(config: Dict[str, Any], raw: Dict[str, Any], out_dir: Path) -> Dict[str, int]:
    """Write Africa and USA CSV extracts for the three TB indicators."""
    names = config["paths"]["raw_csv"]
    africa = list(africa_codes(config))
    usa = [config["geography"]["usa_code"]]
    counts: Dict[str, int] = {}

    inc_header = ["country_code", "country_name", "year", "tb_incidence_per_100k",
                  "low_estimate", "high_estimate"]
    mort_header = ["country_code", "country_name", "year", "tb_mortality_per_100k"]
    succ_header = ["country_code", "country_name", "year", "tb_treatment_success_pct"]

    for prefix, codes in (("", africa), ("usa_", usa)):
        inc = extract_who_incidence(raw["who_tb_incidence"], codes)
        mort = extract_world_bank(raw["wb_tb_mortality"], codes)
        succ = extract_world_bank(raw["wb_tb_treatment_success"], codes)
        for key, header, rows in (("incidence", inc_header, inc),
                                  ("mortality", mort_header, mort),
                                  ("treatment_success", succ_header, succ)):
            file_key = f"{prefix}{key}"
            write_csv(out_dir / names[file_key], header, rows)
            counts[file_key] = len(rows)
    return counts


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def dry_run(config: Dict[str, Any], check_connectivity: bool = False) -> bool:
    """Validate configuration and (optionally) probe each endpoint once."""
    ok = True
    out_dir = PROJECT_ROOT / config["paths"]["raw_dir"]
    logger.info("[dry-run] raw dir: %s (exists=%s)", out_dir, out_dir.exists())
    logger.info("[dry-run] African countries configured: %d", len(africa_codes(config)))
    for name, src in config["data_sources"].items():
        parsed = urlparse(src["url"])
        valid = parsed.scheme == "https" and bool(parsed.netloc)
        ok &= valid
        logger.info("[dry-run] %-24s %s -> %s [%s]", name, src["url"], src["raw_file"],
                    "OK" if valid else "INVALID URL")
    if check_connectivity:
        session = build_session()
        timeout = config["pipeline"].get("request_timeout_seconds", 60)
        for name, src in config["data_sources"].items():
            probe = src["url"]
            if "worldbank" in probe:
                probe = with_query(probe, per_page=1, page=1)
            try:
                # stream=True: only headers are read, the body is not downloaded
                with session.get(probe, timeout=timeout, stream=True) as resp:
                    status = resp.status_code
            except requests.RequestException as exc:
                status = f"ERROR {exc.__class__.__name__}"
            reachable = status == 200
            ok &= reachable
            logger.info("[dry-run] probe %-24s -> %s", name, status)
    return ok


def ingest(config: Dict[str, Any], from_cache: bool = False) -> Dict[str, int]:
    """Download (or reload) all sources and write the CSV extracts.

    Returns a mapping of output name -> row/record count.
    """
    out_dir = raw_dir(config)
    sources = config["data_sources"]
    raw: Dict[str, Any] = {}
    counts: Dict[str, int] = {}

    if from_cache:
        logger.info("Rebuilding extracts from cached raw JSON in %s", out_dir)
        for name, src in sources.items():
            raw[name] = load_json(out_dir / src["raw_file"])
    else:
        fetch_json = make_fetcher(config, build_session())
        failures = []
        for name, src in sources.items():
            target = out_dir / src["raw_file"]
            try:
                if "ghoapi" in src["url"]:
                    payload = fetch_who_gho(src["url"], fetch_json)
                else:
                    payload = fetch_world_bank(src["url"], fetch_json)
                save_json(payload, target)
                raw[name] = payload
            except (requests.RequestException, IngestionError, ValueError) as exc:
                logger.error("Failed to fetch %s: %s", name, exc)
                if target.exists():
                    logger.warning("Falling back to cached %s", target.name)
                    raw[name] = load_json(target)
                else:
                    failures.append(name)
        if failures:
            raise IngestionError(f"No data available for: {', '.join(failures)}")

    for name, payload in raw.items():
        counts[name] = len(payload["value"]) if isinstance(payload, dict) else len(payload[1] or [])
    counts.update(write_extracts(config, raw, out_dir))
    logger.info("Ingestion complete: %s", counts)
    return counts


def main(argv: List[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Ingest TB burden data (WHO GHO + World Bank)")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without writing")
    parser.add_argument("--check-connectivity", action="store_true",
                        help="With --dry-run, probe each API endpoint once")
    parser.add_argument("--from-cache", action="store_true",
                        help="Rebuild CSV extracts from existing raw JSON (no network)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")))
    try:
        if args.dry_run:
            return 0 if dry_run(config, args.check_connectivity) else 1
        ingest(config, from_cache=args.from_cache)
        return 0
    except (IngestionError, FileNotFoundError) as exc:
        logger.error("Ingestion failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
