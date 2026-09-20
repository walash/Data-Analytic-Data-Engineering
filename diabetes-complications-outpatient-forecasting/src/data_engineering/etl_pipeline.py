"""
ETL pipeline: ingest raw multi-source CSVs, validate, clean, and integrate them
into curated processed tables in ``data/processed``.

Components
----------
DataIngestion     : load raw CSVs, validate a sample against Pydantic schemas,
                    record row counts and completeness.
DataQualityChecker: completeness, uniqueness, range and referential-integrity
                    checks producing a structured report.
DataTransformer   : missing-value imputation, outlier capping, type coercion,
                    categorical normalisation and keyword NLP flags from notes.
ETLPipeline       : orchestrates the full flow and persists processed tables.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd

from src.data_engineering.schema import (
    ComplicationOutcome,
    LabResult,
    OutpatientEncounter,
    PatientDemographics,
)
from src.utils.common import get_logger, get_paths, load_config

logger = get_logger("etl")

# Keyword lexicon for note-based NLP flag extraction
NOTE_KEYWORDS = {
    "flag_poor_control": ["hyperglycemia", "rising", "missed", "poor", "uncontrolled", "polyuria"],
    "flag_renal": ["albumin", "egfr", "nephrology", "renal", "microalbuminuria", "creatinine"],
    "flag_retinal": ["retinopathy", "vision", "fundus", "ophthalmology", "blurred"],
    "flag_neuro": ["neuropathy", "tingling", "monofilament", "sensation", "numbness"],
    "flag_cardiac": ["chest", "ecg", "cardiology", "exertion", "angina"],
    "flag_referral": ["referral", "consult", "refer"],
}


class DataIngestion:
    """Load raw CSVs and validate a sample against the schemas."""

    RAW_FILES = {
        "patients": "patients.csv",
        "labs": "labs.csv",
        "outpatient_visits": "outpatient_visits.csv",
        "clinical_notes": "clinical_notes.csv",
        "complications": "complications.csv",
    }
    SCHEMA_MAP = {
        "patients": PatientDemographics,
        "labs": LabResult,
        "outpatient_visits": OutpatientEncounter,
        "complications": ComplicationOutcome,
    }

    def __init__(self):
        self.paths = get_paths()

    def load(self) -> Dict[str, pd.DataFrame]:
        raw = self.paths["data_raw"]
        frames = {}
        for name, fname in self.RAW_FILES.items():
            fp = raw / fname
            if not fp.exists():
                raise FileNotFoundError(f"Missing raw file: {fp}. Run the data generator first.")
            df = pd.read_csv(fp)
            frames[name] = df
            logger.info("Ingested %s: %d rows x %d cols", name, len(df), df.shape[1])
        self._validate_sample(frames)
        return frames

    def _validate_sample(self, frames: Dict[str, pd.DataFrame], n: int = 200) -> None:
        for name, schema in self.SCHEMA_MAP.items():
            df = frames[name]
            sample = df.sample(min(n, len(df)), random_state=0)
            errors = 0
            for rec in sample.to_dict(orient="records"):
                try:
                    schema(**rec)
                except Exception:
                    errors += 1
            rate = 1 - errors / max(1, len(sample))
            logger.info("Schema validity for %-18s: %.1f%% (%d/%d valid)",
                        name, rate * 100, len(sample) - errors, len(sample))
            if rate < 0.95:
                logger.warning("Low schema validity for %s (%.1f%%)", name, rate * 100)


@dataclass
class QualityReport:
    checks: List[dict] = field(default_factory=list)

    def add(self, dataset: str, check: str, passed: bool, detail: str) -> None:
        self.checks.append({"dataset": dataset, "check": check,
                            "passed": bool(passed), "detail": detail})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.checks)

    @property
    def pass_rate(self) -> float:
        if not self.checks:
            return 1.0
        return sum(c["passed"] for c in self.checks) / len(self.checks)


class DataQualityChecker:
    """Completeness, uniqueness, range and referential-integrity checks."""

    RANGES = {
        "labs": {"hba1c": (4.0, 18.0), "egfr": (5, 150), "systolic_bp": (80, 240)},
        "patients": {"age": (18, 95), "bmi": (12, 70)},
    }

    def __init__(self, min_completeness: float | None = None):
        cfg = load_config()
        self.min_completeness = min_completeness or cfg["etl"]["min_completeness"]

    def run(self, frames: Dict[str, pd.DataFrame]) -> QualityReport:
        rep = QualityReport()
        pids = set(frames["patients"]["patient_id"])

        # completeness
        for name, df in frames.items():
            comp = df.notna().mean().min()
            rep.add(name, "completeness", comp >= self.min_completeness,
                    f"min column completeness={comp:.3f} (threshold {self.min_completeness})")

        # uniqueness of primary keys
        rep.add("patients", "unique_patient_id",
                frames["patients"]["patient_id"].is_unique,
                f"{frames['patients']['patient_id'].duplicated().sum()} duplicates")
        rep.add("outpatient_visits", "unique_encounter_id",
                frames["outpatient_visits"]["encounter_id"].is_unique,
                f"{frames['outpatient_visits']['encounter_id'].duplicated().sum()} duplicates")

        # referential integrity
        for name in ["labs", "outpatient_visits", "clinical_notes", "complications"]:
            orphan = (~frames[name]["patient_id"].isin(pids)).sum()
            rep.add(name, "referential_integrity", orphan == 0,
                    f"{orphan} rows reference unknown patient_id")

        # range checks
        for name, cols in self.RANGES.items():
            df = frames[name]
            for col, (lo, hi) in cols.items():
                if col in df:
                    oob = ((df[col] < lo) | (df[col] > hi)).sum()
                    rep.add(name, f"range[{col}]", oob == 0,
                            f"{oob} values outside [{lo},{hi}]")

        logger.info("Data quality pass-rate: %.1f%% (%d checks)",
                    rep.pass_rate * 100, len(rep.checks))
        return rep


class DataTransformer:
    """Clean and integrate raw frames into curated tables."""

    def __init__(self):
        cfg = load_config()
        self.strategy = cfg["etl"]["missing_value_strategy"]
        self.z_thresh = cfg["etl"]["outlier_z_threshold"]

    def clean_numeric(self, df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
        df = df.copy()
        for c in cols:
            if c not in df:
                continue
            if df[c].isna().any():
                fill = df[c].median() if self.strategy == "median" else df[c].mean()
                df[c] = df[c].fillna(fill)
            # cap outliers by z-score (cast to float to avoid dtype clashes)
            mu, sd = df[c].mean(), df[c].std(ddof=0)
            if sd > 0:
                df[c] = df[c].astype(float)
                z = (df[c] - mu) / sd
                df.loc[z > self.z_thresh, c] = mu + self.z_thresh * sd
                df.loc[z < -self.z_thresh, c] = mu - self.z_thresh * sd
        return df

    @staticmethod
    def extract_note_flags(notes: pd.DataFrame) -> pd.DataFrame:
        """Aggregate keyword-based binary flags per patient from clinical notes."""
        txt = notes.groupby("patient_id")["note_text"].apply(lambda s: " ".join(s).lower())
        out = pd.DataFrame({"patient_id": txt.index})
        for flag, kws in NOTE_KEYWORDS.items():
            pattern = re.compile("|".join(re.escape(k) for k in kws))
            out[flag] = txt.apply(lambda t: int(bool(pattern.search(t)))).values
        return out

    def transform(self, frames: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        patients = self.clean_numeric(frames["patients"], ["age", "bmi", "years_since_diagnosis"])
        lab_cols = ["hba1c", "fasting_glucose", "systolic_bp", "diastolic_bp", "ldl",
                    "hdl", "triglycerides", "creatinine", "egfr", "urine_acr"]
        labs = self.clean_numeric(frames["labs"], lab_cols)

        visits = frames["outpatient_visits"].copy()
        visits["visit_date"] = pd.to_datetime(visits["visit_date"])
        visits = visits.drop_duplicates(subset=["encounter_id"])

        note_flags = self.extract_note_flags(frames["clinical_notes"])
        complications = frames["complications"].copy()
        complications["deteriorated_12m"] = complications["deteriorated_12m"].astype(int)

        logger.info("Transformation complete: %d note-flag rows extracted", len(note_flags))
        return {
            "patients_clean": patients,
            "labs_clean": labs,
            "visits_clean": visits,
            "note_flags": note_flags,
            "complications_clean": complications,
        }


class ETLPipeline:
    """Orchestrate ingestion -> quality -> transform -> persist."""

    def __init__(self):
        self.paths = get_paths()

    def run(self) -> Dict[str, pd.DataFrame]:
        logger.info("=== ETL pipeline start ===")
        frames = DataIngestion().load()
        report = DataQualityChecker().run(frames)
        processed = DataTransformer().transform(frames)

        out = self.paths["data_processed"]
        for name, df in processed.items():
            df.to_csv(out / f"{name}.csv", index=False)
        report.to_frame().to_csv(self.paths["reports"] / "data_quality_report.csv", index=False)
        with open(self.paths["reports"] / "data_quality_summary.json", "w") as fh:
            json.dump({"pass_rate": report.pass_rate, "n_checks": len(report.checks)}, fh, indent=2)

        logger.info("Processed tables written to %s", out)
        logger.info("=== ETL pipeline done (quality pass-rate %.1f%%) ===", report.pass_rate * 100)
        return processed


if __name__ == "__main__":
    ETLPipeline().run()
