"""Ingestion module for the AI in Agriculture project.

Fetches raw agricultural and food-security indicators from the World Bank
Open Data API for 15 African countries and the USA, and stores the raw JSON
responses under ``data/raw/``.

Indicators fetched:
    * Cereal yield (kg per hectare)          -> AG.YLD.CREL.KG
    * Agricultural land (% of land area)     -> AG.LND.AGRI.ZS
    * Food production index (2014-16 = 100)  -> AG.PRD.FOOD.XD
    * Fertilizer consumption (kg per hectare)-> AG.CON.FERT.ZS
    * Agriculture value added (% of GDP)     -> NV.AGR.TOTL.ZS
    * Rural population (% of total)          -> SP.RUR.TOTL.ZS

Run:
    python src/ingestion/ingest.py
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingestion")

# 15 African countries (ISO2, semicolon separated as required by the WB API).
COUNTRIES = "NG;GH;KE;ET;ZA;EG;MA;TZ;UG;SN;CM;CI;RW;ZM;MZ"
USA_CODE = "US"

BASE_URL = "https://api.worldbank.org/v2/country"

INDICATORS = {
    "cereal_yield": "AG.YLD.CREL.KG",
    "agricultural_land": "AG.LND.AGRI.ZS",
    "food_production_index": "AG.PRD.FOOD.XD",
    "fertilizer_consumption": "AG.CON.FERT.ZS",
    "agr_value_added": "NV.AGR.TOTL.ZS",
    "rural_population": "SP.RUR.TOTL.ZS",
}

# Mapping of logical output filenames for Africa and USA datasets.
AFRICA_FILES = {
    "cereal_yield": "wb_cereal_yield.json",
    "agricultural_land": "wb_agricultural_land.json",
    "food_production_index": "wb_food_production_index.json",
    "fertilizer_consumption": "wb_fertilizer_consumption.json",
    "agr_value_added": "wb_agr_value_added.json",
    "rural_population": "wb_rural_population.json",
}

USA_FILES = {
    "cereal_yield": "wb_usa_cereal_yield.json",
    "agricultural_land": "wb_usa_agr_land.json",
    "food_production_index": "wb_usa_food_production.json",
    "fertilizer_consumption": "wb_usa_fertilizer.json",
    "agr_value_added": "wb_usa_agr_value_added.json",
    "rural_population": "wb_usa_rural_pop.json",
}

DEFAULT_PARAMS = {
    "format": "json",
    "per_page": "500",
    "date": "2015:2024",
}


def fetch_indicator(
    country_codes,
    indicator_code,
    indicator_name,
    output_dir,
    output_filename,
    max_retries=3,
):
    """Fetch a single indicator for the given countries and save it as JSON.

    Args:
        country_codes: Semicolon separated ISO2 country codes (or a single code).
        indicator_code: World Bank indicator id (e.g. ``AG.YLD.CREL.KG``).
        indicator_name: Human readable indicator name (used for logging).
        output_dir: Directory where the JSON payload should be written.
        output_filename: Target filename for the raw JSON payload.
        max_retries: Number of attempts before giving up (exponential backoff).

    Returns:
        The parsed JSON payload on success, otherwise ``None``.
    """
    url = f"{BASE_URL}/{country_codes}/indicator/{indicator_code}"
    output_path = Path(output_dir) / output_filename

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "Fetching '%s' (%s) [attempt %d/%d]",
                indicator_name,
                indicator_code,
                attempt,
                max_retries,
            )
            response = requests.get(url, params=DEFAULT_PARAMS, timeout=60)
            response.raise_for_status()
            payload = response.json()

            # Basic validation: WB returns [metadata, data_list].
            if not isinstance(payload, list) or len(payload) < 2:
                raise ValueError("Unexpected World Bank response structure")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)

            rows = len(payload[1]) if isinstance(payload[1], list) else 0
            logger.info("Saved %s (%d rows) -> %s", indicator_name, rows, output_path)
            return payload
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "Failed to fetch '%s' (attempt %d/%d): %s",
                indicator_name,
                attempt,
                max_retries,
                exc,
            )
            if attempt < max_retries:
                logger.info("Retrying in %d seconds...", wait)
                time.sleep(wait)
            else:
                logger.error(
                    "Giving up on '%s' after %d attempts", indicator_name, max_retries
                )
    return None


def main():
    """Fetch every indicator for both the African cohort and the USA."""
    raw_dir = Path(os.environ.get("DATA_DIR", "data")) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Raw data directory: %s", raw_dir.resolve())

    success, failure = 0, 0

    # Africa cohort (15 countries).
    logger.info("=== Ingesting African cohort (15 countries) ===")
    for name, code in INDICATORS.items():
        result = fetch_indicator(
            COUNTRIES, code, name, raw_dir, AFRICA_FILES[name]
        )
        if result is not None:
            success += 1
        else:
            failure += 1

    # USA.
    logger.info("=== Ingesting USA ===")
    for name, code in INDICATORS.items():
        result = fetch_indicator(
            USA_CODE, code, f"USA {name}", raw_dir, USA_FILES[name]
        )
        if result is not None:
            success += 1
        else:
            failure += 1

    logger.info("Ingestion complete: %d succeeded, %d failed", success, failure)
    if failure:
        logger.warning("%d indicator(s) could not be fetched.", failure)


if __name__ == "__main__":
    main()
