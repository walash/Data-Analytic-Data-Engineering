"""Data ingestion module for the AI Adoption & Digital Infrastructure pipeline.

This script fetches five digital-infrastructure indicators from the World Bank
Open Data API for 43 African countries plus the United States and writes one
tidy CSV per indicator into ``data/raw/``.

Indicators
----------
* ``IT.NET.USER.ZS``  - Individuals using the Internet (% of population)
* ``IT.CEL.SETS.P2``  - Mobile cellular subscriptions (per 100 people)
* ``IT.NET.BBND.P2``  - Fixed broadband subscriptions (per 100 people)
* ``TX.VAL.TECH.MF.ZS`` - High-technology exports (% of manufactured exports)
* ``GB.XPD.RSDV.GD.ZS`` - R&D expenditure (% of GDP)

The API is queried with the endpoint::

    https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}?format=json&per_page=500&mrv=10

Each request is retried up to three times with exponential backoff. Output CSVs
have the columns: ``country_code, country_name, indicator_id, indicator_name,
year, value``.

Run directly (``python src/ingestion/ingest.py``) or import :func:`ingest` from
an orchestrator such as Airflow. A ``--dry-run`` flag validates configuration
and connectivity without writing files.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

LOGGER = logging.getLogger("ingestion")


def _configure_logging(level: str = "INFO") -> None:
    """Configure root logging once with a consistent format.

    Parameters
    ----------
    level:
        Logging level name (e.g. ``"INFO"`` or ``"DEBUG"``).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def project_root() -> Path:
    """Return the absolute path to the project root directory.

    The root is two levels above this file (``src/ingestion/ingest.py``).
    """
    return Path(__file__).resolve().parents[2]


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load and parse the YAML configuration file.

    Parameters
    ----------
    config_path:
        Optional explicit path to ``config.yaml``. When ``None`` the file is
        resolved relative to the project root or the ``CONFIG_PATH`` env var.

    Returns
    -------
    dict
        Parsed configuration mapping.

    Raises
    ------
    FileNotFoundError
        If the configuration file cannot be located.
    """
    if config_path is None:
        config_path = os.environ.get(
            "CONFIG_PATH", str(project_root() / "config" / "config.yaml")
        )
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    LOGGER.debug("Loaded configuration from %s", path)
    return config


def resolve_raw_dir(config: Dict[str, Any]) -> Path:
    """Resolve and create the raw-data output directory.

    Parameters
    ----------
    config:
        Parsed configuration mapping.

    Returns
    -------
    pathlib.Path
        Absolute path to the raw-data directory (created if missing).
    """
    raw_dir = project_root() / config["paths"]["raw_data"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


def _request_with_retries(
    url: str,
    params: Dict[str, Any],
    max_attempts: int,
    base_delay: float,
    timeout: int,
) -> List[Any]:
    """Perform a GET request with exponential-backoff retry logic.

    Parameters
    ----------
    url:
        Fully-qualified request URL.
    params:
        Query-string parameters.
    max_attempts:
        Maximum number of attempts before giving up.
    base_delay:
        Base delay (seconds) used for exponential backoff.
    timeout:
        Per-request timeout in seconds.

    Returns
    -------
    list
        Parsed JSON payload (a two-element list from the World Bank API).

    Raises
    ------
    RuntimeError
        If every attempt fails.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                raise ValueError(f"Unexpected API payload structure: {payload!r:.200}")
            return payload
        except (requests.RequestException, ValueError) as exc:  # noqa: PERF203
            last_error = exc
            delay = base_delay * (2 ** (attempt - 1))
            LOGGER.warning(
                "Attempt %d/%d failed for %s (%s). Retrying in %.1fs",
                attempt,
                max_attempts,
                url,
                exc,
                delay,
            )
            if attempt < max_attempts:
                time.sleep(delay)
    raise RuntimeError(f"All {max_attempts} attempts failed for {url}: {last_error}")


def fetch_indicator(
    indicator_id: str,
    countries: List[str],
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Fetch every observation for one indicator across all countries.

    Handles World Bank API pagination by requesting successive pages until the
    reported page count is exhausted.

    Parameters
    ----------
    indicator_id:
        World Bank indicator code (e.g. ``"IT.NET.USER.ZS"``).
    countries:
        List of ISO-3 country codes.
    config:
        Parsed configuration mapping.

    Returns
    -------
    list of dict
        Normalised records with keys ``country_code, country_name,
        indicator_id, indicator_name, year, value``.
    """
    api = config["world_bank_api"]
    country_str = ";".join(countries)
    url = f"{api['base_url']}/country/{country_str}/indicator/{indicator_id}"
    pipeline_cfg = config.get("pipeline", {})
    max_attempts = int(pipeline_cfg.get("retry_attempts", 3))
    base_delay = float(pipeline_cfg.get("retry_delay_seconds", 5))
    timeout = int(api.get("timeout_seconds", 30))

    records: List[Dict[str, Any]] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        params = {
            "format": api.get("format", "json"),
            "per_page": api.get("per_page", 500),
            "mrv": api.get("mrv", 10),
            "page": page,
        }
        payload = _request_with_retries(url, params, max_attempts, base_delay, timeout)
        meta, rows = payload[0], payload[1]
        total_pages = int(meta.get("pages", 1) or 1)
        for row in rows or []:
            value = row.get("value")
            records.append(
                {
                    "country_code": (row.get("countryiso3code") or "").strip(),
                    "country_name": (row.get("country") or {}).get("value", ""),
                    "indicator_id": (row.get("indicator") or {}).get("id", indicator_id),
                    "indicator_name": (row.get("indicator") or {}).get("value", ""),
                    "year": row.get("date"),
                    "value": value,
                }
            )
        LOGGER.info(
            "Indicator %s: fetched page %d/%d (%d cumulative rows)",
            indicator_id,
            page,
            total_pages,
            len(records),
        )
        page += 1
    return records


def _write_csv(records: List[Dict[str, Any]], out_path: Path) -> int:
    """Write indicator records to a CSV file, keeping only rows with a value.

    Parameters
    ----------
    records:
        Normalised indicator records.
    out_path:
        Destination CSV path.

    Returns
    -------
    int
        Number of non-null rows written.
    """
    import csv

    fieldnames = [
        "country_code",
        "country_name",
        "indicator_id",
        "indicator_name",
        "year",
        "value",
    ]
    written = 0
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            if record.get("value") is None:
                continue
            writer.writerow({key: record.get(key, "") for key in fieldnames})
            written += 1
    return written


def ingest(config_path: Optional[str] = None, dry_run: bool = False) -> Dict[str, int]:
    """Fetch all configured indicators and persist them to ``data/raw/``.

    Parameters
    ----------
    config_path:
        Optional path to the configuration file.
    dry_run:
        When ``True`` the API is queried for a single indicator to validate
        connectivity but no CSV files are written.

    Returns
    -------
    dict
        Mapping of output file stem to the number of rows written (or fetched
        during a dry run).
    """
    config = load_config(config_path)
    _configure_logging(config.get("pipeline", {}).get("log_level", "INFO"))
    countries = config["countries"]
    indicators = config["indicators"]
    raw_dir = resolve_raw_dir(config)

    LOGGER.info(
        "Starting ingestion for %d countries and %d indicators",
        len(countries),
        len(indicators),
    )

    summary: Dict[str, int] = {}
    for indicator_id, meta in indicators.items():
        file_stem = meta["file"]
        if dry_run:
            LOGGER.info("[dry-run] Validating indicator %s", indicator_id)
            records = fetch_indicator(indicator_id, countries[:2], config)
            summary[file_stem] = len(records)
            LOGGER.info("[dry-run] %s -> %d sample rows", indicator_id, len(records))
            break  # a single indicator is enough to validate connectivity
        records = fetch_indicator(indicator_id, countries, config)
        out_path = raw_dir / f"{file_stem}.csv"
        rows = _write_csv(records, out_path)
        summary[file_stem] = rows
        LOGGER.info("Wrote %d rows to %s", rows, out_path)

    LOGGER.info("Ingestion complete: %s", summary)
    return summary


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments for the ingestion script.

    Parameters
    ----------
    argv:
        Optional argument list (defaults to ``sys.argv``).

    Returns
    -------
    argparse.Namespace
        Parsed arguments with ``config`` and ``dry_run`` attributes.
    """
    parser = argparse.ArgumentParser(description="Ingest World Bank indicators.")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate connectivity without writing CSV files.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point used by the CLI and Airflow ``PythonOperator``.

    Parameters
    ----------
    argv:
        Optional argument list forwarded to :func:`_parse_args`.
    """
    args = _parse_args(argv)
    ingest(config_path=args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
