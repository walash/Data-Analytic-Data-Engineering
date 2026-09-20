"""
etl_pipeline.py
===============

Production-style ETL pipeline for the CVD Risk Stratification & ED Capacity
Planning project. Reads the raw synthetic datasets, validates them against the
Pydantic schemas, cleans / transforms them and writes analysis-ready tables to
``data/processed/``.

Components
----------
* :class:`DataIngestion`     - load raw CSVs, validate against schemas, log
  data-quality metrics on ingest.
* :class:`DataTransformer`   - impute missing values, encode categoricals,
  normalise numerics, and derive keyword-based NLP features from clinical notes.
* :class:`DataQualityChecker`- completeness, uniqueness and range-validation
  checks producing a structured JSON quality report.
* :class:`ETLPipeline`       - orchestrator that wires the three stages together
  and persists all outputs plus a run manifest.

Run directly::

    python -m src.data_engineering.etl_pipeline
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import ValidationError

from .schema import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    ClinicalNote,
    EDVisit,
    PatientRecord,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("etl_pipeline")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
PROCESSED_DIR = os.path.join(PROJECT_ROOT, "data", "processed")

# Keyword lexicon for lightweight clinical-note NLP feature extraction.
NOTE_KEYWORDS: Dict[str, List[str]] = {
    "mentions_chest_pain": ["chest pain", "chest tightness", "chest discomfort", "angina", "substernal"],
    "mentions_dyspnea": ["shortness of breath", "dyspnea", "breathless", "sob"],
    "mentions_diabetes": ["diabetes", "diabetic", "elevated fasting glucose", "hyperglycemia"],
    "mentions_smoking": ["smoker", "smoking", "tobacco", "cessation"],
    "mentions_syncope": ["syncope", "presyncope", "fainting", "loss of consciousness"],
}


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------
@dataclass
class IngestionResult:
    """Container for an ingested dataframe plus validation diagnostics."""

    name: str
    df: pd.DataFrame
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: List[str] = field(default_factory=list)

    @property
    def validity_rate(self) -> float:
        return round(self.valid_rows / self.total_rows, 4) if self.total_rows else 0.0


class DataIngestion:
    """Load raw CSVs and validate each row against the corresponding schema."""

    _SCHEMAS = {
        "patients": PatientRecord,
        "clinical_notes": ClinicalNote,
        "ed_visits": EDVisit,
    }

    def __init__(self, raw_dir: str = RAW_DIR, sample_validation: Optional[int] = None):
        """
        Parameters
        ----------
        raw_dir:
            Directory containing patients.csv / clinical_notes.csv / ed_visits.csv.
        sample_validation:
            If set, only validate a random sample of this many rows per dataset
            (row-by-row Pydantic validation on very large tables can be slow).
            The full dataframe is still returned. ``None`` validates every row.
        """
        self.raw_dir = raw_dir
        self.sample_validation = sample_validation

    def _load_csv(self, name: str) -> pd.DataFrame:
        path = os.path.join(self.raw_dir, f"{name}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Raw dataset '{name}' not found at {path}. "
                "Run the data_generator first."
            )
        df = pd.read_csv(path)
        logger.info("Loaded %-14s %6d rows x %2d cols from %s", name, len(df), df.shape[1], path)
        return df

    def _validate(self, name: str, df: pd.DataFrame) -> Tuple[int, int, List[str]]:
        schema = self._SCHEMAS[name]
        errors: List[str] = []

        if self.sample_validation and len(df) > self.sample_validation:
            check_df = df.sample(self.sample_validation, random_state=0)
        else:
            check_df = df

        valid = 0
        for idx, row in check_df.iterrows():
            try:
                schema(**row.to_dict())
                valid += 1
            except ValidationError as exc:
                if len(errors) < 20:  # cap stored errors
                    errors.append(f"row {idx}: {exc.errors()[0].get('msg', 'invalid')}")
        invalid = len(check_df) - valid
        return valid, invalid, errors

    def ingest(self, name: str) -> IngestionResult:
        df = self._load_csv(name)
        # Parse timestamps where relevant so downstream stages get datetimes.
        for col in ("timestamp", "visit_timestamp"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")

        valid, invalid, errors = self._validate(name, df)
        result = IngestionResult(
            name=name,
            df=df,
            total_rows=len(df),
            valid_rows=valid if not self.sample_validation else valid,
            invalid_rows=invalid,
            errors=errors,
        )
        logger.info(
            "Validated %-14s validity=%.2f%% (invalid=%d, checked=%d)",
            name, 100 * (valid / max(valid + invalid, 1)), invalid, valid + invalid,
        )
        return result

    def ingest_all(self) -> Dict[str, IngestionResult]:
        return {name: self.ingest(name) for name in self._SCHEMAS}


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------
class DataTransformer:
    """Clean, encode, normalise and enrich the ingested data."""

    def __init__(self) -> None:
        # Persisted normalisation statistics (fit on patients numerics).
        self.numeric_means_: Dict[str, float] = {}
        self.numeric_stds_: Dict[str, float] = {}

    # -- patients -------------------------------------------------------- #
    def transform_patients(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # 1. Handle missing values -----------------------------------------
        for col in NUMERIC_FEATURES:
            if col in df.columns and df[col].isna().any():
                median = df[col].median()
                df[col] = df[col].fillna(median)
                logger.info("Imputed %d missing '%s' with median=%.2f",
                            int(df[col].isna().sum()), col, median)
        for col in CATEGORICAL_FEATURES:
            if col in df.columns and df[col].isna().any():
                mode = df[col].mode(dropna=True)
                fill = mode.iloc[0] if not mode.empty else 0
                df[col] = df[col].fillna(fill)

        # 2. Enforce dtypes ------------------------------------------------
        for col in NUMERIC_FEATURES:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in CATEGORICAL_FEATURES:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        # 3. Standard-scale numerics (store stats for reproducibility) -----
        for col in NUMERIC_FEATURES:
            mean = float(df[col].mean())
            std = float(df[col].std(ddof=0)) or 1.0
            self.numeric_means_[col] = mean
            self.numeric_stds_[col] = std
            df[f"{col}_z"] = ((df[col] - mean) / std).round(4)

        # 4. One-hot encode key categoricals (kept alongside raw codes) ----
        ohe = pd.get_dummies(
            df[["chest_pain_type", "rest_ecg", "st_slope", "thal"]].astype("Int64"),
            columns=["chest_pain_type", "rest_ecg", "st_slope", "thal"],
            prefix=["cp", "ecg", "slope", "thal"],
        ).astype(int)
        df = pd.concat([df, ohe], axis=1)

        logger.info("Transformed patients -> %d cols", df.shape[1])
        return df

    # -- clinical notes -------------------------------------------------- #
    def transform_clinical_notes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract keyword-based NLP flags and aggregate to patient level."""
        df = df.copy()
        df["note_text_lower"] = df["note_text"].astype(str).str.lower()

        for feature, keywords in NOTE_KEYWORDS.items():
            pattern = "|".join([kw.replace(" ", r"\s+") for kw in keywords])
            df[feature] = df["note_text_lower"].str.contains(pattern, regex=True).astype(int)

        df["note_length"] = df["note_text"].astype(str).str.split().apply(len)

        agg = (
            df.groupby("patient_id")
            .agg(
                note_count=("note_id", "count"),
                mentions_chest_pain=("mentions_chest_pain", "max"),
                mentions_dyspnea=("mentions_dyspnea", "max"),
                mentions_diabetes=("mentions_diabetes", "max"),
                mentions_smoking=("mentions_smoking", "max"),
                mentions_syncope=("mentions_syncope", "max"),
                avg_note_length=("note_length", "mean"),
            )
            .reset_index()
        )
        agg["avg_note_length"] = agg["avg_note_length"].round(1)
        logger.info("Extracted NLP flags for %d patients from %d notes", len(agg), len(df))
        return agg

    # -- ED visits ------------------------------------------------------- #
    def transform_ed_visits(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean ED visits and derive temporal columns."""
        df = df.copy()
        df["visit_timestamp"] = pd.to_datetime(df["visit_timestamp"], errors="coerce")
        df = df.dropna(subset=["visit_timestamp"])

        df["visit_date"] = df["visit_timestamp"].dt.date
        df["hour"] = df["visit_timestamp"].dt.hour
        df["day_of_week"] = df["visit_timestamp"].dt.dayofweek
        df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
        df["month"] = df["visit_timestamp"].dt.month
        df["year"] = df["visit_timestamp"].dt.year
        df["quarter"] = df["visit_timestamp"].dt.quarter

        # Meteorological season (northern hemisphere).
        season_map = {12: "Winter", 1: "Winter", 2: "Winter",
                      3: "Spring", 4: "Spring", 5: "Spring",
                      6: "Summer", 7: "Summer", 8: "Summer",
                      9: "Fall", 10: "Fall", 11: "Fall"}
        df["season"] = df["month"].map(season_map)

        df["high_acuity"] = (df["triage_level"] <= 2).astype(int)
        df["admitted_flag"] = df["disposition"].isin(
            ["admitted", "icu_admit"]
        ).astype(int)

        logger.info("Transformed ED visits -> %d rows with temporal features", len(df))
        return df


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
class DataQualityChecker:
    """Run completeness, uniqueness and range-validation checks."""

    # Expected numeric ranges for range validation (patients table).
    _RANGES = {
        "age": (18, 100),
        "resting_bp": (80, 220),
        "cholesterol": (100, 600),
        "max_heart_rate": (60, 220),
        "st_depression": (0.0, 7.0),
        "ca_vessels": (0, 3),
        "los_hours": (0.0, 720.0),
        "resources_used": (0, 50),
        "triage_level": (1, 5),
    }

    def completeness(self, df: pd.DataFrame) -> Dict[str, float]:
        return {col: round(1 - df[col].isna().mean(), 4) for col in df.columns}

    def uniqueness(self, df: pd.DataFrame, key: str) -> Dict[str, object]:
        if key not in df.columns:
            return {"key": key, "present": False}
        dup = int(df[key].duplicated().sum())
        return {
            "key": key,
            "present": True,
            "n_unique": int(df[key].nunique()),
            "n_duplicates": dup,
            "is_unique": dup == 0,
        }

    def range_validation(self, df: pd.DataFrame) -> Dict[str, Dict[str, object]]:
        report: Dict[str, Dict[str, object]] = {}
        for col, (lo, hi) in self._RANGES.items():
            if col not in df.columns:
                continue
            series = pd.to_numeric(df[col], errors="coerce")
            out_of_range = int(((series < lo) | (series > hi)).sum())
            report[col] = {
                "min": float(np.nanmin(series)) if len(series) else None,
                "max": float(np.nanmax(series)) if len(series) else None,
                "expected_min": lo,
                "expected_max": hi,
                "out_of_range": out_of_range,
                "passed": out_of_range == 0,
            }
        return report

    def check_dataset(self, name: str, df: pd.DataFrame, key: Optional[str]) -> Dict[str, object]:
        report = {
            "dataset": name,
            "n_rows": int(len(df)),
            "n_cols": int(df.shape[1]),
            "completeness": self.completeness(df),
            "range_validation": self.range_validation(df),
        }
        if key:
            report["uniqueness"] = self.uniqueness(df, key)

        # Overall pass/fail summary.
        completeness_ok = all(v >= 0.99 for v in report["completeness"].values())
        ranges_ok = all(r["passed"] for r in report["range_validation"].values())
        unique_ok = report.get("uniqueness", {}).get("is_unique", True)
        report["summary"] = {
            "completeness_ok": completeness_ok,
            "ranges_ok": ranges_ok,
            "uniqueness_ok": unique_ok,
            "overall_passed": completeness_ok and ranges_ok and unique_ok,
        }
        logger.info(
            "Quality [%s]: completeness_ok=%s ranges_ok=%s unique_ok=%s",
            name, completeness_ok, ranges_ok, unique_ok,
        )
        return report


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class ETLPipeline:
    """Run ingestion -> transformation -> quality checks and persist outputs."""

    def __init__(
        self,
        raw_dir: str = RAW_DIR,
        processed_dir: str = PROCESSED_DIR,
        sample_validation: Optional[int] = 2000,
    ):
        self.raw_dir = raw_dir
        self.processed_dir = processed_dir
        self.ingestion = DataIngestion(raw_dir=raw_dir, sample_validation=sample_validation)
        self.transformer = DataTransformer()
        self.quality = DataQualityChecker()

    def run(self) -> Dict[str, object]:
        os.makedirs(self.processed_dir, exist_ok=True)
        started = datetime.now()
        logger.info("=== ETL pipeline started ===")

        # 1. Ingest ---------------------------------------------------------
        ingested = self.ingestion.ingest_all()
        patients_raw = ingested["patients"].df
        notes_raw = ingested["clinical_notes"].df
        visits_raw = ingested["ed_visits"].df

        # 2. Transform ------------------------------------------------------
        patients_t = self.transformer.transform_patients(patients_raw)
        notes_agg = self.transformer.transform_clinical_notes(notes_raw)
        visits_t = self.transformer.transform_ed_visits(visits_raw)

        # Join patient-level NLP features onto patients table.
        patients_processed = patients_t.merge(notes_agg, on="patient_id", how="left")
        nlp_cols = [c for c in notes_agg.columns if c != "patient_id"]
        patients_processed[nlp_cols] = patients_processed[nlp_cols].fillna(0)

        # 3. Persist processed tables --------------------------------------
        p_out = os.path.join(self.processed_dir, "patients_processed.csv")
        n_out = os.path.join(self.processed_dir, "clinical_notes_features.csv")
        v_out = os.path.join(self.processed_dir, "ed_visits_processed.csv")
        patients_processed.to_csv(p_out, index=False)
        notes_agg.to_csv(n_out, index=False)
        visits_t.to_csv(v_out, index=False)
        logger.info("Saved processed patients -> %s (%d rows)", p_out, len(patients_processed))
        logger.info("Saved note features      -> %s (%d rows)", n_out, len(notes_agg))
        logger.info("Saved processed visits   -> %s (%d rows)", v_out, len(visits_t))

        # 4. Quality checks -------------------------------------------------
        quality_report = {
            "patients": self.quality.check_dataset("patients_processed", patients_processed, "patient_id"),
            "ed_visits": self.quality.check_dataset("ed_visits_processed", visits_t, "visit_id"),
            "clinical_notes": self.quality.check_dataset("clinical_notes_features", notes_agg, "patient_id"),
        }

        # 5. Build run manifest --------------------------------------------
        finished = datetime.now()
        manifest = {
            "run_started_at": started.isoformat(),
            "run_finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 2),
            "ingestion": {
                name: {
                    "total_rows": r.total_rows,
                    "invalid_rows": r.invalid_rows,
                    "validity_rate": r.validity_rate,
                    "sample_errors": r.errors[:5],
                }
                for name, r in ingested.items()
            },
            "outputs": {
                "patients_processed": {"path": p_out, "rows": len(patients_processed)},
                "clinical_notes_features": {"path": n_out, "rows": len(notes_agg)},
                "ed_visits_processed": {"path": v_out, "rows": len(visits_t)},
            },
            "quality_report": quality_report,
        }

        report_path = os.path.join(self.processed_dir, "quality_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        logger.info("Saved quality report -> %s", report_path)
        logger.info("=== ETL pipeline finished in %.2fs ===", manifest["duration_seconds"])
        return manifest


def main() -> None:
    ETLPipeline().run()


if __name__ == "__main__":
    main()
