"""Loading stage for the Africa & USA Railway Networks pipeline.

Creates the SQLite warehouse ``data/railway_networks.db`` (the
``RAILWAY_DB_PATH`` env var overrides the location) and loads the processed
CSVs into four normalised tables plus one convenience view:

* ``country_profiles``     - one row per country: region, latest rail, GDP and LPI values
* ``rail_infrastructure``  - country-year rail route-km with GDP / LPI context
* ``rail_freight``         - country-year rail freight (million ton-km) and density
* ``rail_passengers``      - country-year rail passenger-km and density
* ``v_rail_master``        - view joining the three fact tables to the profiles

The load is idempotent: tables are dropped and re-created on every run.
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("railway.load")

SCHEMA = """
DROP VIEW IF EXISTS v_rail_master;
DROP TABLE IF EXISTS rail_passengers;
DROP TABLE IF EXISTS rail_freight;
DROP TABLE IF EXISTS rail_infrastructure;
DROP TABLE IF EXISTS country_profiles;

CREATE TABLE country_profiles (
    country_code                        TEXT PRIMARY KEY,
    country_name                        TEXT NOT NULL,
    region                              TEXT NOT NULL,
    subregion                           TEXT NOT NULL,
    is_africa                           INTEGER NOT NULL CHECK (is_africa IN (0, 1)),
    has_rail_network                    INTEGER NOT NULL CHECK (has_rail_network IN (0, 1)),
    latest_rail_lines_km                REAL,
    latest_rail_lines_year              INTEGER,
    latest_rail_freight_million_tkm     REAL,
    latest_rail_freight_year            INTEGER,
    latest_rail_passengers_million_km   REAL,
    latest_rail_passengers_year         INTEGER,
    latest_gdp_per_capita_usd           REAL,
    latest_gdp_year                     INTEGER,
    latest_lpi_infrastructure_score     REAL,
    latest_lpi_year                     INTEGER,
    latest_freight_density_mtkm_per_km  REAL
);

CREATE TABLE rail_infrastructure (
    country_code                     TEXT NOT NULL REFERENCES country_profiles(country_code),
    year                             INTEGER NOT NULL,
    rail_lines_km                    REAL,
    rail_lines_km_filled             REAL,
    rail_lines_yoy_pct               REAL,
    gdp_per_capita_usd               REAL,
    lpi_infrastructure_score         REAL,
    lpi_infrastructure_score_filled  REAL,
    PRIMARY KEY (country_code, year)
);

CREATE TABLE rail_freight (
    country_code                 TEXT NOT NULL REFERENCES country_profiles(country_code),
    year                         INTEGER NOT NULL,
    rail_freight_million_tkm     REAL NOT NULL CHECK (rail_freight_million_tkm > 0),
    freight_density_mtkm_per_km  REAL,
    rail_freight_yoy_pct         REAL,
    PRIMARY KEY (country_code, year)
);

CREATE TABLE rail_passengers (
    country_code                   TEXT NOT NULL REFERENCES country_profiles(country_code),
    year                           INTEGER NOT NULL,
    rail_passengers_million_km     REAL NOT NULL CHECK (rail_passengers_million_km > 0),
    passenger_density_mpkm_per_km  REAL,
    freight_share_of_traffic_pct   REAL,
    rail_passengers_yoy_pct        REAL,
    PRIMARY KEY (country_code, year)
);

CREATE INDEX idx_infra_year ON rail_infrastructure(year);
CREATE INDEX idx_freight_year ON rail_freight(year);
CREATE INDEX idx_pass_year ON rail_passengers(year);
CREATE INDEX idx_profiles_region ON country_profiles(region, subregion);

CREATE VIEW v_rail_master AS
SELECT p.country_code, p.country_name, p.region, p.subregion, p.is_africa,
       i.year, i.rail_lines_km, i.rail_lines_km_filled, i.rail_lines_yoy_pct,
       f.rail_freight_million_tkm, f.freight_density_mtkm_per_km,
       s.rail_passengers_million_km, s.passenger_density_mpkm_per_km,
       i.gdp_per_capita_usd, i.lpi_infrastructure_score
FROM rail_infrastructure i
JOIN country_profiles p ON p.country_code = i.country_code
LEFT JOIN rail_freight f ON f.country_code = i.country_code AND f.year = i.year
LEFT JOIN rail_passengers s ON s.country_code = i.country_code AND s.year = i.year;
"""

PROFILE_RENAMES = {
    "rail_lines_km": "latest_rail_lines_km",
    "rail_lines_km_year": "latest_rail_lines_year",
    "rail_freight_million_tkm": "latest_rail_freight_million_tkm",
    "rail_freight_million_tkm_year": "latest_rail_freight_year",
    "rail_passengers_million_km": "latest_rail_passengers_million_km",
    "rail_passengers_million_km_year": "latest_rail_passengers_year",
    "gdp_per_capita_usd": "latest_gdp_per_capita_usd",
    "gdp_per_capita_usd_year": "latest_gdp_year",
    "lpi_infrastructure_score": "latest_lpi_infrastructure_score",
    "lpi_infrastructure_score_year": "latest_lpi_year",
}


def load_config(path: Optional[str] = None) -> Dict:
    """Load the YAML configuration (``CONFIG_PATH`` env var overrides the default)."""
    config_path = Path(path or os.environ.get("CONFIG_PATH", DEFAULT_CONFIG))
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_db_path(config: Dict) -> Path:
    """Return the database path, honouring the ``RAILWAY_DB_PATH`` override."""
    db = Path(os.environ.get("RAILWAY_DB_PATH", config["paths"]["database"]))
    return db if db.is_absolute() else PROJECT_ROOT / db


def _insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    """Insert a DataFrame into an existing table, converting NaN to NULL."""
    if df.empty:
        return 0
    clean = df.astype(object).where(pd.notna(df), None)
    cols = ", ".join(clean.columns)
    marks = ", ".join("?" for _ in clean.columns)
    conn.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
                     clean.itertuples(index=False, name=None))
    return len(clean)


def build_tables(master: pd.DataFrame, profiles: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Split the processed data into the four warehouse tables."""
    profiles = profiles.rename(columns=PROFILE_RENAMES)
    for col in [c for c in profiles.columns if c.endswith("_year")]:
        profiles[col] = profiles[col].astype("Int64")

    infra_cols = ["country_code", "year", "rail_lines_km", "rail_lines_km_filled",
                  "rail_lines_yoy_pct", "gdp_per_capita_usd", "lpi_infrastructure_score",
                  "lpi_infrastructure_score_filled"]
    freight_cols = ["country_code", "year", "rail_freight_million_tkm",
                    "freight_density_mtkm_per_km", "rail_freight_yoy_pct"]
    pass_cols = ["country_code", "year", "rail_passengers_million_km",
                 "passenger_density_mpkm_per_km", "freight_share_of_traffic_pct",
                 "rail_passengers_yoy_pct"]
    return {
        "country_profiles": profiles,
        "rail_infrastructure": master[infra_cols],
        "rail_freight": master.dropna(subset=["rail_freight_million_tkm"])[freight_cols],
        "rail_passengers": master.dropna(subset=["rail_passengers_million_km"])[pass_cols],
    }


def load(config: Dict) -> Dict[str, int]:
    """Create the schema and load all tables; returns row counts per table."""
    paths = config["paths"]
    master_path = PROJECT_ROOT / paths["master_file"]
    profiles_path = PROJECT_ROOT / paths["country_profiles_file"]
    for path in (master_path, profiles_path):
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run the transformation stage first")

    # keep_default_na=False stops Namibia's ISO2 code "NA" from becoming NaN.
    csv_opts = {"dtype": {"country_code": str}, "keep_default_na": False,
                "na_values": ["", "NaN", "nan", "null", "None"]}
    master = pd.read_csv(master_path, **csv_opts)
    profiles = pd.read_csv(profiles_path, **csv_opts)
    tables = build_tables(master, profiles)

    db_path = resolve_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts: Dict[str, int] = {}
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        for name in ["country_profiles", "rail_infrastructure", "rail_freight", "rail_passengers"]:
            counts[name] = _insert(conn, name, tables[name])
            logger.info("Loaded %-20s %5d rows", name, counts[name])
        conn.commit()
    logger.info("SQLite warehouse ready at %s", db_path)
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    parser = argparse.ArgumentParser(description="Load processed railway data into SQLite.")
    parser.add_argument("--config", help="Path to config.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", config["pipeline"].get("log_level", "INFO")).upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    try:
        load(config)
    except (FileNotFoundError, sqlite3.DatabaseError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
