"""
Shared utilities: configuration loading, path resolution, logging, and seeding.

Every module in the framework imports from here so that paths and configuration
are resolved consistently regardless of the current working directory.
"""
from __future__ import annotations

import logging
import os
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import numpy as np
import yaml

# Project root = two levels up from this file (src/utils/common.py -> project root)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


@lru_cache(maxsize=1)
def load_config(config_path: str | os.PathLike | None = None) -> Dict[str, Any]:
    """Load and cache the YAML configuration."""
    path = Path(config_path) if config_path else CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg


def resolve_path(relative: str, create: bool = False) -> Path:
    """Resolve a config-relative path against the project root."""
    p = PROJECT_ROOT / relative
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def get_paths(create: bool = True) -> Dict[str, Path]:
    """Return a dict of resolved project paths, optionally creating them."""
    cfg = load_config()
    paths = {}
    for key, rel in cfg["paths"].items():
        paths[key] = resolve_path(rel, create=create)
    return paths


def set_global_seed(seed: int | None = None) -> int:
    """Seed all relevant RNGs for reproducibility."""
    if seed is None:
        seed = load_config()["project"].get("random_seed", 42)
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
    return seed


def get_logger(name: str = "t2d_framework", level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger that writes to console and logs/pipeline.log."""
    logger = logging.getLogger(name)
    if logger.handlers:  # already configured
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    logs_dir = resolve_path(load_config()["paths"]["logs"], create=True)
    file_handler = logging.FileHandler(logs_dir / "pipeline.log")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


# Canonical orderings used across the framework
COMPLICATION_CLASSES = ["none", "retinopathy", "nephropathy", "neuropathy", "cardiovascular"]
RISK_LEVELS = ["Low", "Medium", "High"]
