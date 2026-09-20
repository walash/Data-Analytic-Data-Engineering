"""
Lightweight data catalog and lineage tracker.

Scans the raw / processed / feature-store directories, records dataset metadata
(row/column counts, schema, size, created-at) and a simple lineage graph, and
persists everything to ``docs/data_catalog.json``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.utils.common import get_logger, get_paths

logger = get_logger("data_catalog")

LINEAGE = {
    "patients.csv": [],
    "labs.csv": [],
    "outpatient_visits.csv": [],
    "clinical_notes.csv": [],
    "complications.csv": [],
    "patients_clean.csv": ["patients.csv"],
    "labs_clean.csv": ["labs.csv"],
    "visits_clean.csv": ["outpatient_visits.csv"],
    "note_flags.csv": ["clinical_notes.csv"],
    "complications_clean.csv": ["complications.csv"],
    "features.parquet": ["patients_clean.csv", "labs_clean.csv", "visits_clean.csv",
                          "note_flags.csv", "complications_clean.csv"],
    "weekly_demand.parquet": ["visits_clean.csv"],
}

DESCRIPTIONS = {
    "patients.csv": "Raw EHR demographic master for the diabetes cohort.",
    "labs.csv": "Raw laboratory panels (glycemic, lipid, renal markers).",
    "outpatient_visits.csv": "Raw outpatient encounters over 24 months.",
    "clinical_notes.csv": "Raw unstructured clinical notes.",
    "complications.csv": "Complication labels and 12-month deterioration flag.",
    "features.parquet": "Modelling-ready wide feature matrix with targets.",
    "weekly_demand.parquet": "Weekly outpatient visit counts (overall + by clinic).",
}


def _profile(fp: Path) -> dict:
    if fp.suffix == ".parquet":
        df = pd.read_parquet(fp)
    else:
        df = pd.read_csv(fp)
    return {
        "name": fp.name,
        "path": str(fp.relative_to(get_paths()["data_raw"].parents[1])),
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "schema": {c: str(t) for c, t in df.dtypes.items()},
        "size_kb": round(fp.stat().st_size / 1024, 1),
        "description": DESCRIPTIONS.get(fp.name, ""),
        "lineage": LINEAGE.get(fp.name, []),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_catalog() -> dict:
    paths = get_paths()
    catalog = {"datasets": [], "generated_at": datetime.now(timezone.utc).isoformat()}
    for folder in [paths["data_raw"], paths["data_processed"], paths["feature_store"]]:
        for fp in sorted(folder.glob("*")):
            if fp.suffix in (".csv", ".parquet") and fp.name != ".gitkeep":
                try:
                    catalog["datasets"].append(_profile(fp))
                except Exception as exc:  # pragma: no cover
                    logger.warning("Could not profile %s: %s", fp, exc)

    out = paths["reports"].parents[1] / "docs" / "data_catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(catalog, fh, indent=2)
    logger.info("Data catalog written: %s (%d datasets)", out, len(catalog["datasets"]))
    return catalog


if __name__ == "__main__":
    build_catalog()
