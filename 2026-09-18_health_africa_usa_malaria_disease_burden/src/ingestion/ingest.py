"""Ingestion module for the Malaria & Infectious Disease Burden pipeline.

This module fetches raw data from two public, no-key APIs:

* **WHO Global Health Observatory (GHO)** — estimated malaria incidence and deaths.
* **World Bank** — current health expenditure (% of GDP), life expectancy at birth,
  and under-5 mortality.

The fetched data is written as CSV files into ``data/raw/``. All HTTP calls use a
``requests.Session`` configured with ``HTTPAdapter`` + ``Retry`` for resilient
ingestion (exponential backoff on transient errors). No credentials are used or
stored — both APIs are public.

Run as a module::

    python -m src.ingestion.ingest
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import pandas as pd
import requests
import yaml
from requests.adapters import HTTPAdapter

try:  # urllib3 v2 and v1 expose Retry from different locations
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("ingest")

# African region parent label used by the WHO GHO API (SpatialDimType == "COUNTRY"
# rows carry a "ParentLocation" attribute of "Africa" for the African region).
AFRICA_PARENT = "Africa"


def _project_root() -> str:
    """Return the absolute path to the project root (two levels above this file)."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def load_config(config_path: Optional[str] = None) -> dict:
    """Load the YAML configuration file.

    Args:
        config_path: Optional explicit path to ``config.yaml``. When omitted the
            file is resolved relative to the project root.

    Returns:
        A dictionary with the parsed configuration.
    """
    if config_path is None:
        config_path = os.path.join(_project_root(), "config", "config.yaml")
    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    LOGGER.info("Loaded configuration from %s", config_path)
    return config


def build_session(retries: int = 5, backoff_factor: float = 2.0) -> requests.Session:
    """Create a ``requests.Session`` with retry/backoff for both HTTP and HTTPS.

    Args:
        retries: Total number of retries for failed requests.
        backoff_factor: Backoff multiplier applied between retry attempts.

    Returns:
        A configured :class:`requests.Session`.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "malaria-disease-burden-pipeline/1.0"})
    return session


def _get_json(session: requests.Session, url: str, timeout: int) -> dict:
    """Perform a GET request and return the decoded JSON payload.

    Args:
        session: The configured HTTP session.
        url: The URL to fetch.
        timeout: Per-request timeout in seconds.

    Returns:
        The parsed JSON response as a dictionary.
    """
    LOGGER.info("GET %s", url)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def resolve_paths(config: dict) -> Dict[str, str]:
    """Resolve absolute input/output paths from the configuration.

    Args:
        config: The parsed configuration dictionary.

    Returns:
        A dictionary with the ``raw`` directory path.
    """
    root = _project_root()
    raw_dir = os.path.join(root, config["paths"]["raw_data_dir"])
    os.makedirs(raw_dir, exist_ok=True)
    return {"raw": raw_dir}


def fetch_who_malaria(
    session: requests.Session,
    base_url: str,
    indicator: str,
    timeout: int,
) -> pd.DataFrame:
    """Fetch a WHO GHO malaria indicator and return African-country rows.

    The GHO API returns a JSON object with a top-level ``value`` array where each
    element is a data point. We keep country-level rows for the African region.

    Args:
        session: The configured HTTP session.
        base_url: The WHO GHO API base URL.
        indicator: The GHO indicator code (e.g. ``MALARIA_EST_INCIDENCE``).
        timeout: Per-request timeout in seconds.

    Returns:
        A DataFrame with the raw indicator observations for African countries.
    """
    url = f"{base_url}/{indicator}"
    payload = _get_json(session, url, timeout)
    records = payload.get("value", [])
    LOGGER.info("WHO %s returned %d raw observations", indicator, len(records))

    rows: List[dict] = []
    for rec in records:
        if rec.get("SpatialDimType") != "COUNTRY":
            continue
        if rec.get("ParentLocation") != AFRICA_PARENT:
            continue
        rows.append(
            {
                "country_code": rec.get("SpatialDim"),
                "year": rec.get("TimeDim"),
                "value_numeric": rec.get("NumericValue"),
                "value_low": rec.get("Low"),
                "value_high": rec.get("High"),
                "parent_region": rec.get("ParentLocation"),
            }
        )
    df = pd.DataFrame(rows)
    LOGGER.info("WHO %s -> %d African country rows", indicator, len(df))
    return df


def _worldbank_batches(iso_codes: List[str], batch_size: int) -> List[str]:
    """Split ISO codes into semicolon-joined batches for the World Bank API.

    Args:
        iso_codes: The full list of ISO-3 country codes.
        batch_size: Maximum number of codes per batch.

    Returns:
        A list of semicolon-separated ISO-code strings.
    """
    batches = []
    for i in range(0, len(iso_codes), batch_size):
        batches.append(";".join(iso_codes[i : i + batch_size]))
    return batches


def fetch_worldbank_indicator(
    session: requests.Session,
    base_url: str,
    indicator: str,
    iso_codes: List[str],
    batch_size: int,
    timeout: int,
    mrv: int = 10,
) -> pd.DataFrame:
    """Fetch a World Bank indicator for a set of countries.

    The World Bank v2 API returns a two-element JSON array: element 0 is pagination
    metadata, element 1 is the list of observations. Countries are batched with
    semicolon-separated ISO codes (max ~12 per request).

    Args:
        session: The configured HTTP session.
        base_url: The World Bank API base URL.
        indicator: The World Bank indicator code.
        iso_codes: List of ISO-3 country codes to fetch.
        batch_size: Maximum codes per request.
        timeout: Per-request timeout in seconds.
        mrv: Most-recent-values count (number of latest years to fetch).

    Returns:
        A DataFrame with columns ``country_code``, ``country_name``, ``year`` and
        ``value``.
    """
    rows: List[dict] = []
    for batch in _worldbank_batches(iso_codes, batch_size):
        url = (
            f"{base_url}/country/{batch}/indicator/{indicator}"
            f"?format=json&per_page=200&mrv={mrv}"
        )
        payload = _get_json(session, url, timeout)
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            LOGGER.warning("World Bank %s returned no data for batch %s", indicator, batch)
            continue
        for rec in payload[1]:
            if rec.get("value") is None:
                continue
            rows.append(
                {
                    "country_code": rec.get("countryiso3code"),
                    "country_name": (rec.get("country") or {}).get("value"),
                    "year": rec.get("date"),
                    "value": rec.get("value"),
                }
            )
    df = pd.DataFrame(rows)
    LOGGER.info("World Bank %s -> %d observations", indicator, len(df))
    return df


def _save_csv(df: pd.DataFrame, path: str) -> None:
    """Write a DataFrame to CSV, logging the destination and row count.

    Args:
        df: The DataFrame to persist.
        path: The destination CSV path.
    """
    df.to_csv(path, index=False)
    LOGGER.info("Wrote %d rows -> %s", len(df), path)


def ingest(config_path: Optional[str] = None) -> None:
    """Run the full ingestion: fetch all sources and write 5 CSVs to ``data/raw/``.

    Args:
        config_path: Optional path to the configuration file.
    """
    config = load_config(config_path)
    paths = resolve_paths(config)
    raw_dir = paths["raw"]

    api = config["api"]
    pipeline = config["pipeline"]
    timeout = pipeline["request_timeout_seconds"]
    batch_size = pipeline["world_bank_batch_size"]

    session = build_session(
        retries=pipeline["retry_attempts"],
        backoff_factor=pipeline["retry_delay_seconds"],
    )

    african = config["countries"]["african_iso_codes"]
    usa = config["countries"]["usa_iso_code"]
    wb_countries = list(african) + [usa]

    # --- WHO GHO: malaria incidence ---
    incidence = fetch_who_malaria(
        session, api["who_gho_base_url"], api["who_incidence_indicator"], timeout
    )
    incidence = incidence.rename(
        columns={
            "value_numeric": "incidence_per_1000",
            "value_low": "low",
            "value_high": "high",
        }
    )
    incidence["country_name"] = incidence["country_code"]
    incidence = incidence[
        ["country_code", "country_name", "year", "incidence_per_1000", "low", "high", "parent_region"]
    ]
    _save_csv(incidence, os.path.join(raw_dir, "malaria_incidence_africa.csv"))

    # --- WHO GHO: malaria deaths ---
    deaths = fetch_who_malaria(
        session, api["who_gho_base_url"], api["who_deaths_indicator"], timeout
    )
    deaths = deaths.rename(
        columns={
            "value_numeric": "deaths_count",
            "value_low": "low",
            "value_high": "high",
        }
    )
    deaths = deaths[
        ["country_code", "year", "deaths_count", "low", "high", "parent_region"]
    ]
    _save_csv(deaths, os.path.join(raw_dir, "malaria_deaths_africa.csv"))

    # --- World Bank indicators ---
    wb_specs = [
        (api["wb_health_expenditure_indicator"], "health_expenditure_pct_gdp", "health_expenditure_pct_gdp"),
        (api["wb_life_expectancy_indicator"], "life_expectancy_years", "life_expectancy.csv"),
        (api["wb_under5_mortality_indicator"], "under5_mortality_per_1000", "under5_mortality.csv"),
    ]
    filenames = {
        "health_expenditure_pct_gdp": "health_expenditure_pct_gdp.csv",
        "life_expectancy_years": "life_expectancy.csv",
        "under5_mortality_per_1000": "under5_mortality.csv",
    }
    for indicator, value_col, _ in wb_specs:
        df = fetch_worldbank_indicator(
            session,
            api["world_bank_base_url"],
            indicator,
            wb_countries,
            batch_size,
            timeout,
        )
        if not df.empty:
            df = df.rename(columns={"value": value_col})
            df = df[["country_code", "country_name", "year", value_col]]
        _save_csv(df, os.path.join(raw_dir, filenames[value_col]))

    LOGGER.info("Ingestion complete. Raw CSVs written to %s", raw_dir)


if __name__ == "__main__":
    ingest()
