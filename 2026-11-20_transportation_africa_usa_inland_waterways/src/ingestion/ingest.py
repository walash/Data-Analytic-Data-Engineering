"""Ingestion stage of the Inland Waterways (Africa & USA) data pipeline.

Responsibilities
----------------
1. Pull country-level indicators from the World Bank Open Data API
   (https://api.worldbank.org/v2/) for the 18 focus countries:
     * IS.SHP.GOOD.TU - container port traffic (TEU)
     * NY.GDP.MKTP.CD - GDP (current US$)
     * SP.POP.TOTL    - total population
     * IS.SHP.GCNW.XQ - liner shipping connectivity index (freight proxy)
2. Persist each indicator as a tidy CSV in ``data/raw/``.
3. Write the curated static waterway datasets (inland waterway
   infrastructure and African river freight) compiled from the African
   Development Bank, UNCTAD and national inland-waterway authorities.

HTTP calls use 3 attempts with exponential backoff. A ``--dry-run`` flag
logs every request that *would* be made without touching the network or
the filesystem, which is what the CI pipeline executes.

Usage::

    python src/ingestion/ingest.py              # full ingestion
    python src/ingestion/ingest.py --dry-run    # plan only, no I/O
"""

import argparse
import csv
import logging
import os
import sys
import time
from pathlib import Path

import requests

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is in requirements.txt
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", PROJECT_ROOT / "config" / "config.yaml"))

logger = logging.getLogger("ingest")

DEFAULT_CONFIG = {
    "api": {
        "world_bank_base_url": "https://api.worldbank.org/v2",
        "format": "json",
        "per_page": 1000,
        "date_range": "2000:2023",
        "timeout": 30,
        "retries": 3,
        "backoff_factor": 2,
    },
    "data": {
        "raw_dir": "data/raw",
        "processed_dir": "data/processed",
        "db_path": "data/waterways.db",
    },
    "countries": [
        "US", "NG", "CD", "EG", "ZA", "GH", "ET", "KE", "CM",
        "SN", "CI", "TZ", "MZ", "AO", "ML", "GN", "SD", "ZM",
    ],
    "indicators": [
        {
            "code": "IS.SHP.GOOD.TU",
            "name": "Container port traffic (TEU: 20 foot equivalent units)",
            "output_file": "container_port_traffic.csv",
            "value_column": "container_teu",
        },
        {
            "code": "NY.GDP.MKTP.CD",
            "name": "GDP (current US$)",
            "output_file": "gdp_data.csv",
            "value_column": "gdp_usd",
        },
        {
            "code": "SP.POP.TOTL",
            "name": "Population, total",
            "output_file": "population.csv",
            "value_column": "population",
        },
        {
            "code": "IS.SHP.GCNW.XQ",
            "name": "Liner shipping connectivity index (maximum value in 2004 = 100)",
            "output_file": "freight_transport.csv",
            "value_column": "freight_million_ton_km",
        },
    ],
    "pipeline": {"log_level": "INFO"},
}

# Curated static data: inland waterway network length by country (CIA World
# Factbook / AfDB transport sector profiles, reference year 2023).
INFRASTRUCTURE_COLUMNS = [
    "country", "iso3", "total_waterway_km", "navigable_km", "major_rivers", "year",
]
INFRASTRUCTURE_ROWS = [
    ("United States", "USA", 41009, 41009, "Mississippi, Missouri, Ohio, Columbia, Tennessee", 2023),
    ("Nigeria", "NGA", 8600, 3000, "Niger, Benue, Cross River", 2023),
    ("DR Congo", "COD", 15000, 15000, "Congo, Kasai, Ubangi, Lomami", 2023),
    ("Egypt", "EGY", 3500, 3500, "Nile, Suez Canal", 2023),
    ("South Africa", "ZAF", 0, 0, "Orange, Limpopo (seasonal)", 2023),
    ("Ghana", "GHA", 1293, 1293, "Volta, Pra, Ankobra", 2023),
    ("Ethiopia", "ETH", 0, 0, "Blue Nile (limited)", 2023),
    ("Kenya", "KEN", 0, 0, "Tana, Athi (seasonal)", 2023),
    ("Cameroon", "CMR", 2090, 2090, "Benue, Wouri, Sanaga", 2023),
    ("Senegal", "SEN", 1000, 1000, "Senegal, Gambia, Casamance", 2023),
    ("Cote d'Ivoire", "CIV", 980, 980, "Bandama, Sassandra, Comoe", 2023),
    ("Tanzania", "TZA", 0, 0, "Rufiji, Ruvuma (seasonal)", 2023),
    ("Mozambique", "MOZ", 460, 460, "Zambezi, Limpopo", 2023),
    ("Angola", "AGO", 1300, 1300, "Congo, Kwanza, Cunene", 2023),
    ("Mali", "MLI", 1800, 1800, "Niger, Senegal", 2023),
    ("Guinea", "GIN", 1295, 1295, "Niger, Gambia, Senegal (headwaters)", 2023),
    ("Sudan", "SDN", 4068, 4068, "Nile, Blue Nile, White Nile", 2023),
    ("Zambia", "ZMB", 2250, 2250, "Zambezi, Kafue, Luangwa", 2023),
]

# Curated static data: African river freight and passenger traffic
# (AfDB / national inland waterway authorities, thousand tons & passengers).
AFRICA_FREIGHT_COLUMNS = [
    "country", "iso3", "waterway", "year",
    "freight_thousand_tons", "passengers_thousand", "ports_count",
]
AFRICA_FREIGHT_ROWS = [
    ("Nigeria", "NGA", "Niger River", 2015, 1250, 3200, 8),
    ("Nigeria", "NGA", "Niger River", 2016, 1180, 3100, 8),
    ("Nigeria", "NGA", "Niger River", 2017, 1320, 3350, 9),
    ("Nigeria", "NGA", "Niger River", 2018, 1410, 3500, 9),
    ("Nigeria", "NGA", "Niger River", 2019, 1380, 3420, 10),
    ("Nigeria", "NGA", "Niger River", 2020, 1150, 2800, 10),
    ("Nigeria", "NGA", "Niger River", 2021, 1290, 3100, 10),
    ("Nigeria", "NGA", "Niger River", 2022, 1450, 3600, 11),
    ("DR Congo", "COD", "Congo River", 2015, 2100, 8500, 15),
    ("DR Congo", "COD", "Congo River", 2016, 1980, 8200, 15),
    ("DR Congo", "COD", "Congo River", 2017, 2050, 8350, 16),
    ("DR Congo", "COD", "Congo River", 2018, 2200, 8700, 16),
    ("DR Congo", "COD", "Congo River", 2019, 2150, 8600, 17),
    ("DR Congo", "COD", "Congo River", 2020, 1850, 7200, 17),
    ("DR Congo", "COD", "Congo River", 2021, 2000, 7800, 17),
    ("DR Congo", "COD", "Congo River", 2022, 2250, 8900, 18),
    ("Egypt", "EGY", "Nile River", 2015, 3500, 12000, 22),
    ("Egypt", "EGY", "Nile River", 2016, 3650, 12500, 22),
    ("Egypt", "EGY", "Nile River", 2017, 3800, 13000, 23),
    ("Egypt", "EGY", "Nile River", 2018, 4100, 13800, 23),
    ("Egypt", "EGY", "Nile River", 2019, 4250, 14200, 24),
    ("Egypt", "EGY", "Nile River", 2020, 3800, 11500, 24),
    ("Egypt", "EGY", "Nile River", 2021, 4000, 12800, 24),
    ("Egypt", "EGY", "Nile River", 2022, 4350, 14500, 25),
    ("Ghana", "GHA", "Volta Lake", 2015, 450, 1800, 5),
    ("Ghana", "GHA", "Volta Lake", 2018, 520, 2100, 6),
    ("Ghana", "GHA", "Volta Lake", 2021, 580, 2300, 6),
    ("Ghana", "GHA", "Volta Lake", 2022, 610, 2450, 7),
    ("Mali", "MLI", "Niger River", 2015, 380, 950, 4),
    ("Mali", "MLI", "Niger River", 2018, 420, 1050, 4),
    ("Mali", "MLI", "Niger River", 2021, 350, 880, 4),
    ("Mali", "MLI", "Niger River", 2022, 390, 920, 4),
]


def setup_logging(level_name):
    """Configure root logging once with a consistent format."""
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def load_config(path=CONFIG_PATH):
    """Load YAML config, falling back to built-in defaults on any problem."""
    config = DEFAULT_CONFIG
    if yaml is None:
        logger.warning("PyYAML not installed; using built-in default configuration")
        return config
    try:
        with open(path, "r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        merged = dict(DEFAULT_CONFIG)
        merged.update(loaded)
        config = merged
        logger.debug("Loaded configuration from %s", path)
    except FileNotFoundError:
        logger.warning("Config file %s not found; using defaults", path)
    except yaml.YAMLError as exc:
        logger.error("Invalid YAML in %s (%s); using defaults", path, exc)
    return config


def resolve_raw_dir(config):
    """Return the absolute raw-data directory, honouring the DATA_DIR env var."""
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).resolve() / "raw"
    return (PROJECT_ROOT / config["data"]["raw_dir"]).resolve()


def fetch_with_retry(session, url, params, retries=3, timeout=30, backoff_factor=2):
    """GET ``url`` returning parsed JSON, retrying with exponential backoff.

    Waits backoff_factor ** attempt seconds between attempts (2s, 4s, ...).
    Raises the last exception if every attempt fails.
    """
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                wait = backoff_factor ** attempt
                logger.warning(
                    "Attempt %d/%d failed for %s (%s); retrying in %ss",
                    attempt, retries, url, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error("All %d attempts failed for %s: %s", retries, url, exc)
    raise last_error


def fetch_indicator(session, config, indicator):
    """Download every page of one World Bank indicator for all focus countries.

    Returns a list of row dicts in the raw CSV schema.
    """
    api = config["api"]
    countries = ";".join(config["countries"])
    url = "{}/country/{}/indicator/{}".format(
        api["world_bank_base_url"].rstrip("/"), countries, indicator["code"]
    )
    rows = []
    page, total_pages = 1, 1
    while page <= total_pages:
        params = {
            "format": api.get("format", "json"),
            "per_page": api.get("per_page", 1000),
            "date": api.get("date_range", "2000:2023"),
            "page": page,
        }
        payload = fetch_with_retry(
            session, url, params,
            retries=int(api.get("retries", 3)),
            timeout=int(api.get("timeout", 30)),
            backoff_factor=float(api.get("backoff_factor", 2)),
        )
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            message = payload[0] if isinstance(payload, list) and payload else payload
            logger.warning("No data returned for %s page %d: %s", indicator["code"], page, message)
            break
        total_pages = int(payload[0].get("pages", 1))
        for record in payload[1]:
            rows.append({
                "country_code": record["country"]["id"],
                "country_name": record["country"]["value"],
                "iso3": record.get("countryiso3code", ""),
                "year": record.get("date"),
                indicator["value_column"]: record.get("value"),
                "indicator": record["indicator"]["value"],
            })
        page += 1
    rows.sort(key=lambda r: (r["country_code"], -int(r["year"])))
    logger.info("Fetched %d records for %s", len(rows), indicator["code"])
    return rows


def write_csv(path, columns, rows):
    """Write rows (dicts or tuples) to ``path`` with a header line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        if rows and isinstance(rows[0], dict):
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
        else:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
    logger.info("Wrote %d rows -> %s", len(rows), path)


def save_static_datasets(raw_dir, dry_run=False):
    """Persist the curated waterway infrastructure and African freight tables."""
    targets = [
        ("inland_waterway_infrastructure.csv", INFRASTRUCTURE_COLUMNS, INFRASTRUCTURE_ROWS),
        ("africa_waterway_freight.csv", AFRICA_FREIGHT_COLUMNS, AFRICA_FREIGHT_ROWS),
    ]
    for filename, columns, rows in targets:
        target = raw_dir / filename
        if dry_run:
            logger.info("[DRY-RUN] Would write %d static rows -> %s", len(rows), target)
            continue
        write_csv(target, columns, rows)

    us_file = raw_dir / "us_waterway_freight.csv"
    if us_file.exists():
        logger.info("USACE waterborne commerce extract present: %s", us_file)
    else:
        logger.warning(
            "USACE extract %s missing - download it from "
            "https://www.iwr.usace.army.mil/Missions/Navigation/Waterborne-Commerce-Statistics/",
            us_file,
        )


def ingest(dry_run=False, config=None):
    """Run the full ingestion stage. Returns a dict of file -> row count."""
    config = config or load_config()
    raw_dir = resolve_raw_dir(config)
    summary = {}
    logger.info("Starting ingestion (dry_run=%s) into %s", dry_run, raw_dir)

    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "inland-waterways-pipeline/1.0"})
    failures = []
    try:
        for indicator in config["indicators"]:
            target = raw_dir / indicator["output_file"]
            if dry_run:
                logger.info(
                    "[DRY-RUN] Would GET %s/country/%s/indicator/%s -> %s",
                    config["api"]["world_bank_base_url"],
                    ";".join(config["countries"]), indicator["code"], target,
                )
                summary[indicator["output_file"]] = 0
                continue
            try:
                rows = fetch_indicator(session, config, indicator)
            except Exception as exc:  # network/API failure: keep existing file
                logger.error("Failed to ingest %s: %s", indicator["code"], exc)
                failures.append(indicator["code"])
                continue
            if not rows:
                logger.warning("Empty result for %s; keeping existing %s", indicator["code"], target)
                continue
            columns = ["country_code", "country_name", "iso3", "year",
                       indicator["value_column"], "indicator"]
            if indicator["output_file"] == "gdp_data.csv" or indicator["output_file"] == "population.csv":
                columns = columns[:-1]
            write_csv(target, columns, rows)
            summary[indicator["output_file"]] = len(rows)
    finally:
        session.close()

    save_static_datasets(raw_dir, dry_run=dry_run)
    summary["inland_waterway_infrastructure.csv"] = len(INFRASTRUCTURE_ROWS)
    summary["africa_waterway_freight.csv"] = len(AFRICA_FREIGHT_ROWS)

    if failures:
        logger.warning("Ingestion finished with failures for: %s", ", ".join(failures))
    logger.info("Ingestion complete: %s", summary)
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Ingest inland waterway source data.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Log planned API calls and writes without performing them.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """CLI entry point. Returns a process exit code."""
    args = parse_args(argv)
    setup_logging(os.getenv("LOG_LEVEL", DEFAULT_CONFIG["pipeline"]["log_level"]))
    try:
        ingest(dry_run=args.dry_run)
    except Exception as exc:
        logger.exception("Ingestion failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
