"""Ingestion stage of the Urban Transit & BRT Africa-USA pipeline.

This module pulls every raw input the pipeline needs and lands it, untouched
apart from flattening, in ``data/raw/`` using timestamped filenames so that
each run is auditable and reproducible:

* ITDP-derived BRT system inventory (``brt_systems_africa_usa.csv``), read
  from the local path configured in ``config/config.yaml``
  (source: https://brtdata.org/).
* Four World Bank WDI indicators fetched from the public REST API
  (https://api.worldbank.org/v2):

  - SP.URB.TOTL        -> urban population
  - SP.URB.GROW        -> urban population growth (annual %)
  - NY.GDP.PCAP.CD     -> GDP per capita (current US$)
  - SP.URB.TOTL.IN.ZS  -> urban population (% of total)

World Bank responses are paginated JSON; every page is requested with retry
logic (3 attempts, exponential backoff) and the result is filtered to African
countries plus the United States, dropping regional aggregates.

Run directly with ``python src/ingestion/ingest.py``.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("ingestion")

# Output file stem and value column name for each configured indicator key.
INDICATOR_OUTPUTS = {
    "urban_population": ("wb_urban_population", "urban_population"),
    "urban_growth_rate": ("wb_urban_growth_rate", "urban_growth_rate_pct"),
    "gdp_per_capita": ("wb_gdp_per_capita", "gdp_per_capita_usd"),
    "urban_pct_total": ("wb_urban_pct_total", "urban_pct_of_total"),
}

# ISO3 codes of the 54 African countries plus the United States.
AFRICA_ISO3 = {
    "DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CPV", "CMR", "CAF", "TCD",
    "COM", "COD", "COG", "CIV", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH",
    "GAB", "GMB", "GHA", "GIN", "GNB", "KEN", "LSO", "LBR", "LBY", "MDG",
    "MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA",
    "STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO",
    "TUN", "UGA", "ZMB", "ZWE",
}
TARGET_ISO3 = AFRICA_ISO3 | {"USA"}

BRT_COLUMNS = [
    "city", "country", "iso3", "continent", "system_name", "year_opened",
    "corridor_length_km", "daily_ridership", "num_stations", "fleet_size",
    "status", "operator", "source",
]


def load_config(path=CONFIG_PATH):
    """Load the YAML pipeline configuration.

    Args:
        path: Path to ``config.yaml``.

    Returns:
        dict: Parsed configuration.
    """
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(config):
    """Configure root logging from the ``logging`` block of the config.

    Args:
        config: Parsed configuration dictionary.
    """
    log_cfg = config.get("logging", {})
    logging.basicConfig(
        level=getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO),
        format=log_cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
    )


def raw_data_dir(config):
    """Resolve (and create) the raw data directory.

    The ``DATA_DIR`` environment variable, when set, overrides the parent data
    directory so the pipeline works inside Docker volume mounts.

    Args:
        config: Parsed configuration dictionary.

    Returns:
        Path: Absolute path to ``data/raw``.
    """
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        path = Path(data_dir) / "raw"
    else:
        path = PROJECT_ROOT / config["paths"]["raw_data"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_timestamp():
    """Return a UTC timestamp string used to suffix raw filenames.

    Returns:
        str: Timestamp formatted as ``YYYYMMDDTHHMMSSZ``.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def request_with_retry(url, params, timeout, max_retries=3, backoff_base=2.0):
    """Perform an HTTP GET with exponential-backoff retries.

    Args:
        url: Endpoint URL.
        params: Query-string parameters.
        timeout: Per-request timeout in seconds.
        max_retries: Number of attempts before giving up.
        backoff_base: Base of the exponential wait (1s, 2s, 4s, ...).

    Returns:
        Any: Decoded JSON payload.

    Raises:
        requests.RequestException: If every attempt fails.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            wait = backoff_base ** (attempt - 1)
            logger.warning(
                "Attempt %d/%d failed for %s (%s). Retrying in %.1fs",
                attempt, max_retries, url, exc, wait,
            )
            time.sleep(wait)
    logger.error("All %d attempts failed for %s", max_retries, url)
    raise requests.RequestException(f"Failed to fetch {url}: {last_error}")


def fetch_world_bank_indicator(indicator_code, value_column, config=None):
    """Fetch one World Bank indicator for Africa + USA as a flat DataFrame.

    All pages of ``/country/all/indicator/{code}`` are requested; aggregates
    (regions, income groups) are removed by keeping only target ISO3 codes.

    Args:
        indicator_code: WDI indicator code, e.g. ``SP.URB.TOTL``.
        value_column: Name to give the indicator value column.
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        pandas.DataFrame: Columns ``country_id, country_name, iso3, year,
        <value_column>``.
    """
    config = config or load_config()
    wb = config["world_bank"]
    url = f"{wb['base_url']}/country/all/indicator/{indicator_code}"
    params = {"format": "json", "per_page": wb.get("per_page", 500), "mrv": wb.get("mrv", 5), "page": 1}
    timeout = wb.get("timeout_seconds", 30)
    max_retries = wb.get("max_retries", 3)

    records = []
    total_pages = 1
    while params["page"] <= total_pages:
        payload = request_with_retry(url, dict(params), timeout, max_retries)
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            logger.warning("Unexpected/empty payload for %s page %d", indicator_code, params["page"])
            break
        total_pages = int(payload[0].get("pages", 1))
        for row in payload[1]:
            iso3 = row.get("countryiso3code")
            if iso3 not in TARGET_ISO3:
                continue
            records.append({
                "country_id": row.get("country", {}).get("id"),
                "country_name": row.get("country", {}).get("value"),
                "iso3": iso3,
                "year": row.get("date"),
                value_column: row.get("value"),
            })
        logger.info("%s: page %d/%d processed", indicator_code, params["page"], total_pages)
        params["page"] += 1

    df = pd.DataFrame(records, columns=["country_id", "country_name", "iso3", "year", value_column])
    logger.info("%s: %d Africa/USA records fetched", indicator_code, len(df))
    return df


def fetch_brt_systems_data(config=None):
    """Read the ITDP-derived BRT system inventory from its local path.

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        pandas.DataFrame: BRT systems with the expected schema.

    Raises:
        FileNotFoundError: If the source CSV is missing.
        ValueError: If required columns are absent.
    """
    config = config or load_config()
    data_dir = os.getenv("DATA_DIR")
    local_path = Path(config["brt_data"]["local_path"])
    if data_dir:
        path = Path(data_dir) / "raw" / local_path.name
    else:
        path = PROJECT_ROOT / local_path
    if not path.exists():
        raise FileNotFoundError(f"BRT source file not found: {path}")
    df = pd.read_csv(path)
    missing = set(BRT_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"BRT file missing columns: {sorted(missing)}")
    logger.info("Loaded %d BRT systems from %s (source: %s)", len(df), path, config["brt_data"]["source_url"])
    return df


def save_raw(df, stem, out_dir, timestamp):
    """Write a DataFrame to ``<out_dir>/<stem>_<timestamp>.csv``.

    Args:
        df: DataFrame to write.
        stem: Base filename without extension.
        out_dir: Destination directory.
        timestamp: Run timestamp suffix.

    Returns:
        Path: Path of the written file.
    """
    path = Path(out_dir) / f"{stem}_{timestamp}.csv"
    df.to_csv(path, index=False)
    logger.info("Saved %d rows -> %s", len(df), path)
    return path


def run_ingestion(config=None):
    """Execute the full ingestion stage.

    The BRT inventory is snapshotted with a timestamp; each World Bank
    indicator is fetched and saved. A failed indicator is logged and skipped
    so a transient API outage does not discard the other sources (transform
    then falls back to the most recent file available).

    Args:
        config: Parsed configuration (loaded from disk when ``None``).

    Returns:
        dict: Mapping of dataset name to written file path.
    """
    config = config or load_config()
    out_dir = raw_data_dir(config)
    timestamp = run_timestamp()
    written = {}

    brt_df = fetch_brt_systems_data(config)
    written["brt_systems"] = save_raw(brt_df, "brt_systems_africa_usa", out_dir, timestamp)

    for key, code in config["world_bank"]["indicators"].items():
        stem, value_col = INDICATOR_OUTPUTS[key]
        try:
            df = fetch_world_bank_indicator(code, value_col, config)
        except requests.RequestException as exc:
            logger.error("Skipping %s (%s): %s", key, code, exc)
            continue
        if df.empty:
            logger.warning("No data returned for %s (%s); nothing saved", key, code)
            continue
        written[key] = save_raw(df, stem, out_dir, timestamp)

    logger.info("Ingestion complete: %d datasets written", len(written))
    return written


def cleanup_old_snapshots(out_dir, keep=3):
    """Remove all but the newest ``keep`` timestamped snapshots per dataset.

    Args:
        out_dir: Raw data directory.
        keep: Number of snapshots to retain per dataset stem.

    Returns:
        int: Number of files removed.
    """
    removed = 0
    stems = ["brt_systems_africa_usa"] + [s for s, _ in INDICATOR_OUTPUTS.values()]
    for stem in stems:
        snapshots = sorted(Path(out_dir).glob(f"{stem}_2*.csv"))
        for old in snapshots[:-keep] if keep > 0 else snapshots:
            old.unlink()
            removed += 1
    return removed


def main():
    """CLI entry point: configure logging, ingest, prune old snapshots."""
    config = load_config()
    setup_logging(config)
    try:
        written = run_ingestion(config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Ingestion failed: %s", exc)
        sys.exit(1)
    removed = cleanup_old_snapshots(raw_data_dir(config))
    logger.info("Pruned %d old snapshots", removed)
    for name, path in written.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
