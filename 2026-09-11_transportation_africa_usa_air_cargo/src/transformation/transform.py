"""Data transformation module for the Africa-USA Air Cargo project.

This module reads the raw snapshots produced by ``src/ingestion/ingest.py`` and
produces clean, analysis-ready CSV files in ``data/processed``.

Processing steps:
    1. Load the OurAirports reference CSV and split it into African airports
       (``continent == 'AF'``) and US airports (``iso_country == 'US'``).
    2. Parse the header-less OpenFlights ``routes.dat`` file.
    3. Parse the header-less OpenFlights ``airports.dat`` file to map IATA codes
       to countries/continents so that routes can be geo-classified.
    4. Identify Africa <-> USA routes (source in Africa and destination in the
       USA, or vice versa).
    5. Parse the World Bank JSON payloads into tidy long-format tables for both
       the air-freight and air-passenger indicators.
    6. Clean nulls, cast types, and deduplicate before writing outputs.

Outputs (written to ``data/processed``):
    * ``african_airports.csv``
    * ``us_airports.csv``
    * ``africa_usa_routes.csv``
    * ``air_freight_stats.csv``
    * ``air_passenger_stats.csv``
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("transform")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"

# Column names for the header-less OpenFlights files.
ROUTES_COLUMNS = [
    "airline",
    "airline_id",
    "src_airport",
    "src_airport_id",
    "dst_airport",
    "dst_airport_id",
    "codeshare",
    "stops",
    "equipment",
]

OF_AIRPORTS_COLUMNS = [
    "airport_id",
    "name",
    "city",
    "country",
    "iata",
    "icao",
    "lat",
    "lon",
    "alt",
    "timezone",
    "dst",
    "tz_db",
    "type",
    "source",
]


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Load the YAML project configuration.

    Args:
        config_path: Path to ``config.yaml``.

    Returns:
        Parsed configuration dictionary (empty dict on failure).
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        LOGGER.warning("Could not load config (%s); using defaults.", exc)
        return {}


def get_dirs(config: dict) -> tuple[Path, Path]:
    """Resolve the raw and processed data directories.

    Args:
        config: Parsed project configuration.

    Returns:
        A ``(raw_dir, processed_dir)`` tuple of absolute paths. The processed
        directory is created if it does not already exist.
    """
    paths = config.get("paths", {})
    raw_dir = (PROJECT_ROOT / paths.get("raw", "data/raw")).resolve()
    processed_dir = (
        PROJECT_ROOT / paths.get("processed", "data/processed")
    ).resolve()
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def load_ourairports(raw_dir: Path) -> pd.DataFrame:
    """Load the OurAirports reference CSV.

    Args:
        raw_dir: Directory containing ``airports_ourairports.csv``.

    Returns:
        A DataFrame of all airports with normalised string columns.
    """
    path = raw_dir / "airports_ourairports.csv"
    LOGGER.info("Reading %s", path)
    df = pd.read_csv(path, dtype=str, low_memory=False)
    # Cast coordinate columns to numeric where present.
    for col in ("latitude_deg", "longitude_deg", "elevation_ft"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def split_airports(
    airports: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split reference airports into African and US subsets.

    Args:
        airports: The full OurAirports DataFrame.

    Returns:
        A ``(african_airports, us_airports)`` tuple. Both frames are
        deduplicated on the ``ident`` column.
    """
    african = airports[airports["continent"] == "AF"].copy()
    us = airports[airports["iso_country"] == "US"].copy()

    african = african.drop_duplicates(subset=["ident"])
    us = us.drop_duplicates(subset=["ident"])

    LOGGER.info(
        "Split airports: %d African, %d US.", len(african), len(us)
    )
    return african, us


def load_routes(raw_dir: Path) -> pd.DataFrame:
    """Parse the header-less OpenFlights ``routes.dat`` file.

    Args:
        raw_dir: Directory containing ``openflights_routes.dat``.

    Returns:
        A cleaned routes DataFrame with ``stops`` cast to integer and ``\\N``
        sentinels replaced by NA.
    """
    path = raw_dir / "openflights_routes.dat"
    LOGGER.info("Reading %s", path)
    df = pd.read_csv(
        path,
        header=None,
        names=ROUTES_COLUMNS,
        dtype=str,
        na_values=["\\N"],
    )
    df["stops"] = pd.to_numeric(df["stops"], errors="coerce").fillna(0).astype(int)
    df = df.dropna(subset=["src_airport", "dst_airport"])
    df = df.drop_duplicates()
    LOGGER.info("Parsed %d routes.", len(df))
    return df


def load_openflights_airports(raw_dir: Path) -> pd.DataFrame:
    """Parse the header-less OpenFlights ``airports.dat`` file.

    Args:
        raw_dir: Directory containing ``openflights_airports.dat``.

    Returns:
        A DataFrame indexed for lookup, retaining rows with a valid IATA code.
    """
    path = raw_dir / "openflights_airports.dat"
    LOGGER.info("Reading %s", path)
    df = pd.read_csv(
        path,
        header=None,
        names=OF_AIRPORTS_COLUMNS,
        dtype=str,
        na_values=["\\N"],
    )
    df = df[df["iata"].notna() & (df["iata"] != "")]
    df = df.drop_duplicates(subset=["iata"])
    return df


def build_iata_lookup(
    african: pd.DataFrame, us: pd.DataFrame
) -> tuple[set, set]:
    """Build sets of African and US IATA codes from OurAirports data.

    Args:
        african: African airports subset.
        us: US airports subset.

    Returns:
        A ``(africa_iata, us_iata)`` tuple of sets containing non-empty IATA
        codes for each region.
    """
    africa_iata = set(
        african.loc[african["iata_code"].notna(), "iata_code"]
        .astype(str)
        .str.strip()
    )
    us_iata = set(
        us.loc[us["iata_code"].notna(), "iata_code"].astype(str).str.strip()
    )
    africa_iata.discard("")
    us_iata.discard("")
    LOGGER.info(
        "IATA lookup built: %d African codes, %d US codes.",
        len(africa_iata),
        len(us_iata),
    )
    return africa_iata, us_iata


def identify_africa_usa_routes(
    routes: pd.DataFrame, africa_iata: set, us_iata: set
) -> pd.DataFrame:
    """Filter routes that connect Africa and the USA in either direction.

    Args:
        routes: Parsed OpenFlights routes.
        africa_iata: Set of African airport IATA codes.
        us_iata: Set of US airport IATA codes.

    Returns:
        A DataFrame of Africa <-> USA routes with an added ``direction`` column
        describing the travel direction.
    """
    src = routes["src_airport"]
    dst = routes["dst_airport"]

    africa_to_us = src.isin(africa_iata) & dst.isin(us_iata)
    us_to_africa = src.isin(us_iata) & dst.isin(africa_iata)

    selected = routes[africa_to_us | us_to_africa].copy()
    selected["direction"] = "unknown"
    selected.loc[africa_to_us[selected.index], "direction"] = "Africa->USA"
    selected.loc[us_to_africa[selected.index], "direction"] = "USA->Africa"

    selected = selected.drop_duplicates()
    LOGGER.info("Identified %d Africa-USA routes.", len(selected))
    return selected


def parse_worldbank_json(
    raw_dir: Path, filename: str, value_name: str
) -> pd.DataFrame:
    """Parse a World Bank indicator JSON payload into a tidy long table.

    The World Bank API returns a two-element JSON array: metadata at index 0 and
    the observation records at index 1.

    Args:
        raw_dir: Directory containing the JSON file.
        filename: Name of the World Bank JSON file.
        value_name: Name to assign to the value column (e.g. ``freight_mt_km``).

    Returns:
        A tidy DataFrame with columns ``country``, ``country_iso3``, ``year``,
        and ``value_name``. Rows with a null value are dropped.
    """
    path = raw_dir / filename
    LOGGER.info("Reading %s", path)
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    # The World Bank API returns [metadata, [records...]].
    if isinstance(raw, list) and len(raw) > 1 and isinstance(raw[1], list):
        records = raw[1]
    elif isinstance(raw, list) and raw and isinstance(raw[0], list):
        records = raw[0]
    else:
        records = raw if isinstance(raw, list) else []

    rows = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        country = rec.get("country", {}) or {}
        rows.append(
            {
                "country": country.get("value"),
                "country_iso3": rec.get("countryiso3code"),
                "year": rec.get("date"),
                value_name: rec.get("value"),
            }
        )

    df = pd.DataFrame(rows)
    df = df.dropna(subset=[value_name])
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df[value_name] = pd.to_numeric(df[value_name], errors="coerce")
    df = df.dropna(subset=["year", value_name])
    df = df.drop_duplicates(subset=["country_iso3", "year"])
    LOGGER.info("Parsed %d World Bank rows from %s.", len(df), filename)
    return df


# ISO3 country codes for the 54 African nations, used to flag World Bank rows
# (the World Bank dataset mixes country and aggregate/region rows).
AFRICA_ISO3 = {
    "DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CPV", "CMR", "CAF", "TCD",
    "COM", "COG", "COD", "CIV", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH",
    "GAB", "GMB", "GHA", "GIN", "GNB", "KEN", "LSO", "LBR", "LBY", "MDG",
    "MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA",
    "STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO",
    "TUN", "UGA", "ZMB", "ZWE",
}


def tag_region(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``region`` column classifying each World Bank row.

    Args:
        df: A parsed World Bank long-format table.

    Returns:
        The same DataFrame with an added ``region`` column whose value is
        ``"Africa"``, ``"USA"``, or ``"Other"``.
    """
    def classify(iso3: object) -> str:
        if iso3 in AFRICA_ISO3:
            return "Africa"
        if iso3 == "USA":
            return "USA"
        return "Other"

    df = df.copy()
    df["region"] = df["country_iso3"].apply(classify)
    return df


def transform(config: Optional[dict] = None) -> dict:
    """Run the full transformation pipeline.

    Args:
        config: Optional pre-loaded configuration. When ``None`` the default
            config file is loaded.

    Returns:
        A mapping of output filename to the number of rows written.
    """
    config = config or load_config()
    raw_dir, processed_dir = get_dirs(config)

    # --- Airports ---------------------------------------------------------- #
    airports = load_ourairports(raw_dir)
    african, us = split_airports(airports)
    africa_iata, us_iata = build_iata_lookup(african, us)

    african_out = processed_dir / "african_airports.csv"
    us_out = processed_dir / "us_airports.csv"
    african.to_csv(african_out, index=False)
    us.to_csv(us_out, index=False)

    # --- Routes ------------------------------------------------------------ #
    routes = load_routes(raw_dir)
    au_routes = identify_africa_usa_routes(routes, africa_iata, us_iata)
    routes_out = processed_dir / "africa_usa_routes.csv"
    au_routes.to_csv(routes_out, index=False)

    # Persist the full cleaned routes set as well (used for connection counts).
    all_routes_out = processed_dir / "all_routes.csv"
    routes.to_csv(all_routes_out, index=False)

    # --- World Bank -------------------------------------------------------- #
    freight = parse_worldbank_json(
        raw_dir, "worldbank_air_freight.json", "freight_mt_km"
    )
    freight = tag_region(freight)
    freight_out = processed_dir / "air_freight_stats.csv"
    freight.to_csv(freight_out, index=False)

    passengers = parse_worldbank_json(
        raw_dir, "worldbank_air_passengers.json", "passengers"
    )
    passengers = tag_region(passengers)
    passengers_out = processed_dir / "air_passenger_stats.csv"
    passengers.to_csv(passengers_out, index=False)

    summary = {
        african_out.name: len(african),
        us_out.name: len(us),
        routes_out.name: len(au_routes),
        all_routes_out.name: len(routes),
        freight_out.name: len(freight),
        passengers_out.name: len(passengers),
    }
    LOGGER.info("Transformation complete: %s", summary)
    return summary


def main() -> int:
    """Entry point for command-line execution.

    Returns:
        Process exit code (``0`` on success).
    """
    transform()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
