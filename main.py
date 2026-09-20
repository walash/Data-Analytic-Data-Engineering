#!/usr/bin/env python3
"""
main.py
=======

Unified command-line entrypoint for the **CVD Risk Stratification & ED Capacity
Planning System**.

It orchestrates the four pipeline stages and can run any of them individually or
all of them end-to-end. Logging is emitted to both the console and
``logs/pipeline.log``, and each stage reports its wall-clock duration.

Usage
-----
    python main.py --pipeline data_engineering   # generate data + ETL + features
    python main.py --pipeline eda                # exploratory data analysis
    python main.py --pipeline modeling           # train + evaluate + register
    python main.py --pipeline ed_capacity        # forecast + allocate + optimise
    python main.py --pipeline full               # run everything in order
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime

# --------------------------------------------------------------------------- #
# Paths & logging
# --------------------------------------------------------------------------- #
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

LOGS_DIR = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOGS_DIR, "pipeline.log")

logger = logging.getLogger("cvd_pipeline")


def _configure_logging(level: str = "INFO") -> None:
    """Configure root logging to console + rotating-safe file handler."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(log_level)
    # Avoid duplicate handlers on repeated invocations.
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(console)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(file_handler)


# --------------------------------------------------------------------------- #
# Stage runners
# --------------------------------------------------------------------------- #
def run_data_engineering() -> None:
    """Generate synthetic data, run ETL, build the feature store + catalog."""
    from src.data_engineering import data_catalog, data_generator
    from src.data_engineering.etl_pipeline import ETLPipeline
    from src.data_engineering.feature_engineering import FeatureEngineer

    logger.info("[data_engineering] 1/4 Generating synthetic datasets ...")
    data_generator.main([])

    logger.info("[data_engineering] 2/4 Running ETL pipeline ...")
    ETLPipeline().run()

    logger.info("[data_engineering] 3/4 Building feature store ...")
    FeatureEngineer().run()

    logger.info("[data_engineering] 4/4 Generating data catalog ...")
    try:
        data_catalog.main()
    except Exception as exc:  # non-fatal: catalog is a documentation artefact
        logger.warning("Data catalog generation skipped: %s", exc)


def run_eda() -> None:
    """Run descriptive stats, statistical tests, and generate figures/report."""
    from src.eda.eda_report import EDAReport

    logger.info("[eda] Running full exploratory data analysis ...")
    EDAReport().run()


def run_modeling() -> None:
    """Feature selection, training/tuning, evaluation, calibration + registry."""
    from src.modeling.run_pipeline import main as modeling_main

    logger.info("[modeling] Training and evaluating models ...")
    modeling_main()


def run_ed_capacity() -> None:
    """Demand forecasting, resource allocation, LP optimisation, dashboard."""
    from src.ed_capacity.ed_pipeline import EDPipeline

    logger.info("[ed_capacity] Running ED capacity-planning pipeline ...")
    EDPipeline().run()


STAGES = {
    "data_engineering": run_data_engineering,
    "eda": run_eda,
    "modeling": run_modeling,
    "ed_capacity": run_ed_capacity,
}
FULL_ORDER = ["data_engineering", "eda", "modeling", "ed_capacity"]


# --------------------------------------------------------------------------- #
# Timing helpers
# --------------------------------------------------------------------------- #
def _run_stage(name: str) -> float:
    """Run a single stage, returning its duration in seconds."""
    logger.info("=" * 70)
    logger.info(">>> STAGE START: %s", name)
    logger.info("=" * 70)
    start = time.perf_counter()
    STAGES[name]()
    elapsed = time.perf_counter() - start
    logger.info("<<< STAGE DONE : %s  (%.2fs)", name, elapsed)
    return elapsed


def _run(pipeline: str) -> int:
    stages = FULL_ORDER if pipeline == "full" else [pipeline]
    timings: dict[str, float] = {}
    overall_start = time.perf_counter()

    for name in stages:
        try:
            timings[name] = _run_stage(name)
        except Exception:
            logger.exception("Stage '%s' FAILED — aborting pipeline.", name)
            return 1

    total = time.perf_counter() - overall_start

    logger.info("=" * 70)
    logger.info("TIMING SUMMARY")
    for name, secs in timings.items():
        logger.info("  %-18s %8.2fs", name, secs)
    logger.info("  %-18s %8.2fs", "TOTAL", total)
    logger.info("=" * 70)
    logger.info("Pipeline '%s' completed successfully at %s", pipeline,
                datetime.now().isoformat(timespec="seconds"))
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="CVD Risk Stratification & ED Capacity Planning — pipeline runner.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--pipeline",
        required=True,
        choices=["data_engineering", "eda", "modeling", "ed_capacity", "full"],
        help="Which pipeline stage to run.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.log_level)
    logger.info("Log file: %s", LOG_FILE)
    return _run(args.pipeline)


if __name__ == "__main__":
    raise SystemExit(main())
