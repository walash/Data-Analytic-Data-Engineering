"""
data_catalog.py
===============

A lightweight, file-based data catalog for the CVD Risk Stratification & ED
Capacity Planning project. Provides metadata management and dataset lineage
tracking without any external catalog service - the catalog persists to
``docs/data_catalog.json``.

Capabilities
------------
* Register datasets with rich metadata: name, path, layer (raw/processed/
  feature_store), row/column counts, inferred schema, size on disk, description,
  ``created_at`` and content fingerprint.
* Track **lineage** - which upstream datasets produced which downstream ones -
  as a directed edge list, enabling simple impact / provenance analysis.
* Auto-profile any CSV / Parquet file to populate metadata.
* Rebuild the entire catalog for the project in one call
  (:meth:`DataCatalog.build_default_catalog`).

Run directly to (re)build the catalog for the standard project layout::

    python -m src.data_engineering.data_catalog
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("data_catalog")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
CATALOG_PATH = os.path.join(DOCS_DIR, "data_catalog.json")


class DataCatalog:
    """Metadata + lineage registry persisted as JSON."""

    def __init__(self, catalog_path: str = CATALOG_PATH):
        self.catalog_path = catalog_path
        self.catalog: Dict[str, object] = {
            "catalog_version": "1.0",
            "project": "CVD Risk Stratification & ED Capacity Planning",
            "updated_at": None,
            "datasets": {},   # name -> metadata dict
            "lineage": [],    # list of {"source": .., "target": .., "process": ..}
        }
        if os.path.exists(catalog_path):
            self._load()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            with open(self.catalog_path, "r", encoding="utf-8") as fh:
                self.catalog = json.load(fh)
            logger.info("Loaded existing catalog with %d datasets",
                        len(self.catalog.get("datasets", {})))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load catalog (%s); starting fresh.", exc)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
        self.catalog["updated_at"] = datetime.now().isoformat()
        with open(self.catalog_path, "w", encoding="utf-8") as fh:
            json.dump(self.catalog, fh, indent=2, default=str)
        logger.info("Saved catalog -> %s (%d datasets, %d lineage edges)",
                    self.catalog_path,
                    len(self.catalog["datasets"]),
                    len(self.catalog["lineage"]))

    # ------------------------------------------------------------------ #
    # Profiling helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _read_any(path: str) -> Optional[pd.DataFrame]:
        if not os.path.exists(path):
            return None
        try:
            if path.endswith(".parquet"):
                return pd.read_parquet(path)
            return pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001 - profiling must not crash catalog
            logger.warning("Failed to profile %s: %s", path, exc)
            return None

    @staticmethod
    def _file_fingerprint(path: str, block_size: int = 1 << 20) -> Optional[str]:
        """Return a short md5 fingerprint of the file contents (first ~8MB)."""
        if not os.path.exists(path):
            return None
        md5 = hashlib.md5()
        read = 0
        with open(path, "rb") as fh:
            while read < 8 * (1 << 20):
                chunk = fh.read(block_size)
                if not chunk:
                    break
                md5.update(chunk)
                read += len(chunk)
        return md5.hexdigest()[:16]

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register_dataset(
        self,
        name: str,
        path: str,
        layer: str,
        description: str,
        profile: bool = True,
    ) -> Dict[str, object]:
        """Register (or update) a dataset entry, auto-profiling if requested."""
        rel_path = os.path.relpath(path, PROJECT_ROOT)
        meta: Dict[str, object] = {
            "name": name,
            "path": rel_path,
            "layer": layer,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "exists": os.path.exists(path),
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "fingerprint": self._file_fingerprint(path),
            "row_count": None,
            "column_count": None,
            "schema": {},
        }

        if profile:
            df = self._read_any(path)
            if df is not None:
                meta["row_count"] = int(len(df))
                meta["column_count"] = int(df.shape[1])
                meta["schema"] = {c: str(t) for c, t in df.dtypes.items()}

        self.catalog["datasets"][name] = meta
        logger.info("Registered dataset '%s' [%s] rows=%s cols=%s",
                    name, layer, meta["row_count"], meta["column_count"])
        return meta

    def add_lineage(self, source: str, target: str, process: str) -> None:
        """Record a directed lineage edge source --(process)--> target."""
        edge = {"source": source, "target": target, "process": process}
        if edge not in self.catalog["lineage"]:
            self.catalog["lineage"].append(edge)
            logger.info("Lineage: %s --[%s]--> %s", source, process, target)

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #
    def get_dataset(self, name: str) -> Optional[Dict[str, object]]:
        return self.catalog["datasets"].get(name)

    def upstream_of(self, name: str) -> List[str]:
        return [e["source"] for e in self.catalog["lineage"] if e["target"] == name]

    def downstream_of(self, name: str) -> List[str]:
        return [e["target"] for e in self.catalog["lineage"] if e["source"] == name]

    def summary(self) -> pd.DataFrame:
        rows = [
            {
                "name": m["name"],
                "layer": m["layer"],
                "rows": m["row_count"],
                "cols": m["column_count"],
                "size_kb": round((m["size_bytes"] or 0) / 1024, 1),
                "exists": m["exists"],
            }
            for m in self.catalog["datasets"].values()
        ]
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ #
    # Full project catalog build
    # ------------------------------------------------------------------ #
    def build_default_catalog(self) -> "DataCatalog":
        """Register all standard project datasets and their lineage."""
        raw = os.path.join(PROJECT_ROOT, "data", "raw")
        proc = os.path.join(PROJECT_ROOT, "data", "processed")
        fs = os.path.join(PROJECT_ROOT, "data", "feature_store")

        # --- raw layer ---
        self.register_dataset(
            "patients_raw", os.path.join(raw, "patients.csv"), "raw",
            "Synthetic structured EHR records (UCI Heart Disease-like distributions).")
        self.register_dataset(
            "clinical_notes_raw", os.path.join(raw, "clinical_notes.csv"), "raw",
            "Synthetic unstructured clinical notes (1-4 per patient).")
        self.register_dataset(
            "ed_visits_raw", os.path.join(raw, "ed_visits.csv"), "raw",
            "Synthetic 24-month ED visit event log with seasonal/diurnal patterns.")

        # --- processed layer ---
        self.register_dataset(
            "patients_processed", os.path.join(proc, "patients_processed.csv"), "processed",
            "Cleaned, encoded, normalised patients joined with NLP note flags.")
        self.register_dataset(
            "clinical_notes_features", os.path.join(proc, "clinical_notes_features.csv"), "processed",
            "Patient-level keyword NLP features aggregated from clinical notes.")
        self.register_dataset(
            "ed_visits_processed", os.path.join(proc, "ed_visits_processed.csv"), "processed",
            "Cleaned ED visits enriched with temporal + acuity features.")

        # --- feature store ---
        self.register_dataset(
            "feature_store", os.path.join(fs, "features.parquet"), "feature_store",
            "Model-ready wide feature vector (Framingham score, comorbidity index, ED aggregates).")
        self.register_dataset(
            "ed_demand_timeseries", os.path.join(fs, "ed_demand_timeseries.parquet"), "feature_store",
            "Daily ED demand per risk group with rolling 7/30-day averages for capacity planning.")

        # --- lineage edges ---
        self.add_lineage("patients_raw", "patients_processed", "etl_pipeline")
        self.add_lineage("clinical_notes_raw", "clinical_notes_features", "etl_pipeline")
        self.add_lineage("clinical_notes_features", "patients_processed", "etl_pipeline")
        self.add_lineage("ed_visits_raw", "ed_visits_processed", "etl_pipeline")
        self.add_lineage("patients_processed", "feature_store", "feature_engineering")
        self.add_lineage("ed_visits_processed", "feature_store", "feature_engineering")
        self.add_lineage("ed_visits_processed", "ed_demand_timeseries", "feature_engineering")
        self.add_lineage("patients_processed", "ed_demand_timeseries", "feature_engineering")

        self.save()
        return self


def main() -> None:
    catalog = DataCatalog().build_default_catalog()
    print("\nData Catalog Summary")
    print("=" * 60)
    print(catalog.summary().to_string(index=False))


if __name__ == "__main__":
    main()
