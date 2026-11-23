"""Loading stage of the Africa-USA skilled worker migration pipeline.

Loads the processed CSVs into a SQLite database (default
``data/migration_analytics.db``; override with the ``DB_PATH`` env var) with
three tables:

* ``countries``            - dimension: ISO3, name, subregion and period summary
* ``migration_indicators`` - fact: one row per country-year with raw,
  derived and normalised indicators (FK -> countries)
* ``yearly_summary``       - one row per year aggregating the ten African
  countries, benchmarked against USA net migration. ``gross_africa_outflow``
  sums the absolute net migration of net-sending countries only, and
  ``africa_outflow_vs_usa_inflow_pct`` expresses it relative to the USA net
  inflow - a scale comparison, not the share of emigrants who went to the US
  (World Bank net migration carries no destination breakdown)

The load is idempotent: tables are dropped and recreated in a single
transaction, then indexes are built for the queries in ``sql/queries.sql``.
``generate_report`` (``--report`` flag, also used by the Airflow DAG) writes a
Markdown run summary to ``data/processed/pipeline_report.md``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"

logger = logging.getLogger("loading")

SCHEMA = {
    "countries": """
        CREATE TABLE countries (
            iso3                            TEXT PRIMARY KEY,
            country_name                    TEXT NOT NULL,
            subregion                       TEXT NOT NULL,
            skilled_worker_rank             INTEGER,
            first_year                      INTEGER,
            last_year                       INTEGER,
            total_net_migration             REAL,
            avg_net_migration_rate_per_1000 REAL,
            net_emigration_years            INTEGER,
            cumulative_remittances_usd      REAL,
            avg_remittances_pct_gdp         REAL,
            remittances_cagr_pct            REAL,
            latest_gdp_per_capita           REAL,
            avg_tertiary_enrollment         REAL,
            latest_population               REAL,
            avg_migration_intensity_index   REAL,
            avg_skilled_worker_proxy_score  REAL
        )""",
    "migration_indicators": """
        CREATE TABLE migration_indicators (
            iso3                          TEXT NOT NULL REFERENCES countries(iso3),
            year                          INTEGER NOT NULL,
            net_migration                 REAL,
            remittances_pct_gdp           REAL,
            remittances_usd               REAL,
            gdp_per_capita                REAL,
            tertiary_enrollment           REAL,
            tertiary_enrollment_imputed   INTEGER NOT NULL DEFAULT 0,
            population                    REAL,
            remittances_per_capita        REAL,
            net_migration_rate_per_1000   REAL,
            emigration_rate_per_1000      REAL,
            is_net_emigration             INTEGER NOT NULL DEFAULT 0,
            migration_intensity_index     REAL,
            skilled_worker_proxy_score    REAL,
            remittances_usd_yoy_pct       REAL,
            net_migration_yoy_change      REAL,
            data_complete                 INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (iso3, year)
        )""",
    "yearly_summary": """
        CREATE TABLE yearly_summary (
            year                              INTEGER PRIMARY KEY,
            countries_reporting               INTEGER NOT NULL,
            total_net_migration               REAL,
            net_emigration_countries          INTEGER,
            total_remittances_usd             REAL,
            avg_remittances_pct_gdp           REAL,
            avg_gdp_per_capita                REAL,
            avg_tertiary_enrollment           REAL,
            total_population                  REAL,
            avg_migration_intensity_index     REAL,
            avg_skilled_worker_proxy_score    REAL,
            usa_net_migration                 REAL,
            gross_africa_outflow              REAL,
            africa_outflow_vs_usa_inflow_pct  REAL
        )""",
}

INDEXES = [
    "CREATE INDEX idx_mi_year ON migration_indicators(year)",
    "CREATE INDEX idx_mi_emigration ON migration_indicators(is_net_emigration, year)",
    "CREATE INDEX idx_countries_subregion ON countries(subregion)",
]


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load the YAML configuration (``CONFIG_PATH`` env var takes priority)."""
    config_path = Path(path or os.getenv("CONFIG_PATH") or DEFAULT_CONFIG)
    with open(config_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def setup_logging(config: dict[str, Any]) -> None:
    """Configure root logging from config, honouring the LOG_LEVEL env var."""
    log_cfg = config.get("logging", {})
    level = os.getenv("LOG_LEVEL", log_cfg.get("level", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format=log_cfg.get("format", "%(asctime)s %(levelname)s %(message)s"),
        datefmt=log_cfg.get("datefmt"),
    )


def resolve(path: str) -> Path:
    """Resolve a config path relative to the project root."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def db_path(config: dict[str, Any]) -> Path:
    """Database location: ``DB_PATH`` env var, else ``paths.database``."""
    return resolve(os.getenv("DB_PATH") or config["paths"]["database"])


def read_csv(path: Path, required: bool = True) -> pd.DataFrame:
    """Read a processed CSV, raising a clear error when a required file is absent."""
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Processed file not found: {path} (run the transformation stage first)")
        logger.warning("Optional file missing: %s", path)
        return pd.DataFrame()
    return pd.read_csv(path)


def to_records(df: pd.DataFrame, columns: list[str]) -> list[tuple]:
    """Convert a frame to SQLite-ready tuples (NaN -> NULL, numpy -> Python)."""
    subset = df[columns].astype(object).where(df[columns].notna(), None)
    return [tuple(v.item() if isinstance(v, np.generic) else v for v in row)
            for row in subset.itertuples(index=False, name=None)]


def insert(conn: sqlite3.Connection, table: str, df: pd.DataFrame, columns: list[str]) -> int:
    """Bulk insert the given columns of ``df`` into ``table``."""
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        to_records(df, columns),
    )
    logger.info("Loaded %-22s %4d rows", table, len(df))
    return len(df)


def build_yearly_summary(panel: pd.DataFrame, usa: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the country-year panel to one row per year with the USA benchmark."""
    yearly = panel.groupby("year").agg(
        countries_reporting=("iso3", "nunique"),
        total_net_migration=("net_migration", lambda s: s.sum(min_count=1)),
        net_emigration_countries=("is_net_emigration", "sum"),
        total_remittances_usd=("remittances_usd", lambda s: s.sum(min_count=1)),
        avg_remittances_pct_gdp=("remittances_pct_gdp", "mean"),
        avg_gdp_per_capita=("gdp_per_capita", "mean"),
        avg_tertiary_enrollment=("tertiary_enrollment", "mean"),
        total_population=("population", lambda s: s.sum(min_count=1)),
        avg_migration_intensity_index=("migration_intensity_index", "mean"),
        avg_skilled_worker_proxy_score=("skilled_worker_proxy_score", "mean"),
    ).reset_index()
    outflow = panel.loc[panel["net_migration"] < 0].groupby("year")["net_migration"].sum().abs()
    yearly["gross_africa_outflow"] = yearly["year"].map(outflow).fillna(0.0)
    if not usa.empty:
        yearly = yearly.merge(usa, on="year", how="left")
    else:
        yearly["usa_net_migration"] = np.nan
    yearly["africa_outflow_vs_usa_inflow_pct"] = np.where(
        yearly["usa_net_migration"] > 0,
        yearly["gross_africa_outflow"] / yearly["usa_net_migration"] * 100,
        np.nan,
    )
    return yearly.round(4)


def load(config: dict[str, Any]) -> Path:
    """Run the loading stage and return the database path."""
    paths = config["paths"]
    panel = read_csv(resolve(paths["processed_csv"]))
    summary = read_csv(resolve(paths["summary_csv"]))
    usa = read_csv(resolve(paths["usa_csv"]), required=False)

    for col in ["tertiary_enrollment_imputed", "is_net_emigration", "data_complete"]:
        panel[col] = panel[col].astype(bool).astype(int)
    countries = summary.rename(columns={"country": "country_name"})
    yearly = build_yearly_summary(panel, usa)

    database = db_path(config)
    database.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            for table in ["migration_indicators", "yearly_summary", "countries"]:
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            for ddl in SCHEMA.values():
                conn.execute(ddl)
            insert(conn, "countries", countries, _columns(conn, "countries"))
            insert(conn, "migration_indicators", panel, _columns(conn, "migration_indicators"))
            insert(conn, "yearly_summary", yearly, _columns(conn, "yearly_summary"))
            for ddl in INDEXES:
                conn.execute(ddl)
        orphans = conn.execute("PRAGMA foreign_key_check").fetchall()
        if orphans:
            raise RuntimeError(f"Foreign key violations after load: {orphans[:5]}")
    finally:
        conn.close()
    logger.info("Database ready at %s", database)
    return database


def generate_report(config: dict[str, Any]) -> Path:
    """Write a Markdown summary of the loaded database and return its path."""
    database = db_path(config)
    if not database.exists():
        raise FileNotFoundError(f"Database not found: {database} (run the loading stage first)")
    with sqlite3.connect(database) as conn:
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SCHEMA}
        ranking = pd.read_sql(
            "SELECT skilled_worker_rank AS rank, country_name AS country, "
            "ROUND(avg_skilled_worker_proxy_score, 1) AS skilled_worker_score, "
            "ROUND(avg_net_migration_rate_per_1000, 2) AS net_migration_per_1000, "
            "ROUND(cumulative_remittances_usd / 1e9, 1) AS cumulative_remittances_bn "
            "FROM countries ORDER BY skilled_worker_rank", conn)
        yearly = pd.read_sql(
            "SELECT year, countries_reporting, net_emigration_countries, "
            "ROUND(total_remittances_usd / 1e9, 1) AS remittances_bn, usa_net_migration "
            "FROM yearly_summary ORDER BY year DESC LIMIT 5", conn)
        reporting = conn.execute(
            "SELECT year, COUNT(remittances_usd) FROM migration_indicators GROUP BY year ORDER BY year DESC LIMIT 5"
        ).fetchall()

    def table(df: pd.DataFrame) -> str:
        header = "| " + " | ".join(df.columns) + " |"
        rule = "|" + "---|" * len(df.columns)
        rows = ["| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
                for row in df.itertuples(index=False)]
        return "\n".join([header, rule, *rows])

    lines = [
        "# Africa-USA Skilled Worker Migration - Pipeline Report",
        "",
        f"Generated: {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M UTC}  ",
        f"Database: `{database.name}`",
        "",
        "## Table row counts",
        "",
        *[f"- `{t}`: {n}" for t, n in counts.items()],
        "",
        "## Skilled-worker proxy ranking",
        "",
        table(ranking),
        "",
        "## Most recent years",
        "",
        table(yearly),
        "",
        "Countries with remittance data per year: "
        + ", ".join(f"{y}: {n}/10" for y, n in reporting),
        "",
    ]
    report = resolve(config["paths"]["report_file"])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Report written to %s", report)
    return report


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the column names of a table in declaration order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Load processed migration data into SQLite")
    parser.add_argument("--config", help="Path to config.yaml")
    parser.add_argument("--report", action="store_true", help="Also write the Markdown pipeline report")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    setup_logging(config)
    try:
        load(config)
        if args.report:
            generate_report(config)
    except Exception as exc:  # noqa: BLE001 - top-level guard for CLI exit code
        logger.exception("Loading failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
