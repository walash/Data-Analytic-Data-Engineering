"""
Command-line entry point for the End-to-End Healthcare Data Engineering
Framework (Type 2 Diabetes complications & outpatient load forecasting).

Usage
-----
    python main.py --pipeline data_engineering
    python main.py --pipeline eda
    python main.py --pipeline modeling
    python main.py --pipeline forecasting
    python main.py --pipeline full        # run everything in order
"""
from __future__ import annotations

import argparse
import sys
import time

from src.utils.common import get_logger, set_global_seed

logger = get_logger("main")


def run_data_engineering() -> None:
    from src.data_engineering import data_generator, etl_pipeline, feature_engineering, data_catalog
    data_generator.generate()
    etl_pipeline.ETLPipeline().run()
    feature_engineering.build_feature_store()
    data_catalog.build_catalog()


def run_eda() -> None:
    from src.eda import eda_report
    eda_report.EDAReport().run()


def run_modeling() -> None:
    from src.modeling import run_pipeline
    run_pipeline.run()


def run_forecasting() -> None:
    from src.forecasting import forecasting_pipeline
    forecasting_pipeline.run()


PIPELINES = {
    "data_engineering": run_data_engineering,
    "eda": run_eda,
    "modeling": run_modeling,
    "forecasting": run_forecasting,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="T2D complications & outpatient load forecasting framework.")
    parser.add_argument(
        "--pipeline", required=True,
        choices=[*PIPELINES.keys(), "full"],
        help="Which pipeline stage to run.")
    parser.add_argument("--seed", type=int, default=42, help="Global random seed.")
    args = parser.parse_args(argv)

    set_global_seed(args.seed)
    stages = list(PIPELINES) if args.pipeline == "full" else [args.pipeline]

    t0 = time.time()
    for stage in stages:
        logger.info(">>> Running stage: %s", stage)
        try:
            PIPELINES[stage]()
        except Exception:
            logger.exception("Stage '%s' failed", stage)
            return 1
    logger.info("All requested stages complete in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
