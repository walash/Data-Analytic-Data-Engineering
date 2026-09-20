"""
schema.py
=========

Typed data contracts for the Cardiovascular Disease (CVD) Risk Stratification &
Emergency Department (ED) Capacity Planning project.

All raw and processed datasets in the data-engineering layer are validated against
the Pydantic models defined here. Centralising the schema provides:

* A single source of truth for column names, types and valid ranges.
* Automatic validation / coercion during ETL ingestion.
* Self-documenting enums for categorical clinical variables (mirroring the
  encoding used by the UCI Heart Disease dataset).
* Reusable constants (column lists, categorical/numeric groupings) consumed by
  the ETL, feature-engineering and data-catalog modules.

The clinical feature encoding follows the well-known UCI Heart Disease dataset
(Cleveland database) so that the synthetic generator produces statistically
plausible records.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, IntEnum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Categorical enumerations (UCI Heart Disease encoding)
# ---------------------------------------------------------------------------
class Sex(IntEnum):
    """Biological sex. 1 = male, 0 = female (UCI encoding)."""

    FEMALE = 0
    MALE = 1


class ChestPainType(IntEnum):
    """Chest pain type (cp)."""

    TYPICAL_ANGINA = 0
    ATYPICAL_ANGINA = 1
    NON_ANGINAL = 2
    ASYMPTOMATIC = 3


class FastingBloodSugar(IntEnum):
    """Fasting blood sugar > 120 mg/dl (fbs). 1 = true, 0 = false."""

    FALSE = 0
    TRUE = 1


class RestECG(IntEnum):
    """Resting electrocardiographic results (restecg)."""

    NORMAL = 0
    ST_T_ABNORMALITY = 1
    LV_HYPERTROPHY = 2


class ExerciseAngina(IntEnum):
    """Exercise-induced angina (exang). 1 = yes, 0 = no."""

    NO = 0
    YES = 1


class STSlope(IntEnum):
    """Slope of the peak exercise ST segment (slope)."""

    UPSLOPING = 0
    FLAT = 1
    DOWNSLOPING = 2


class Thalassemia(IntEnum):
    """Thalassemia (thal). 1 = normal, 2 = fixed defect, 3 = reversible defect."""

    NORMAL = 1
    FIXED_DEFECT = 2
    REVERSIBLE_DEFECT = 3


class RiskLevel(str, Enum):
    """Derived cardiovascular risk stratification bucket."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class NoteType(str, Enum):
    """Category of unstructured clinical note."""

    ADMISSION = "admission"
    PROGRESS = "progress"
    DISCHARGE = "discharge"
    CONSULT = "consult"
    NURSING = "nursing"


class TriageLevel(IntEnum):
    """Emergency Severity Index (ESI) triage level (1 = most urgent)."""

    RESUSCITATION = 1
    EMERGENT = 2
    URGENT = 3
    LESS_URGENT = 4
    NON_URGENT = 5


class Disposition(str, Enum):
    """ED visit disposition / outcome."""

    DISCHARGED = "discharged"
    ADMITTED = "admitted"
    ICU = "icu_admit"
    TRANSFERRED = "transferred"
    LWBS = "left_without_being_seen"
    DECEASED = "deceased"


# ---------------------------------------------------------------------------
# Core record schemas
# ---------------------------------------------------------------------------
class PatientRecord(BaseModel):
    """A single patient's structured clinical / EHR record.

    Field encodings follow the UCI Heart Disease (Cleveland) dataset. ``target``
    is the binary presence of heart disease (1 = disease, 0 = no disease) while
    ``risk_level`` is the derived Low/Medium/High stratification.
    """

    patient_id: str = Field(..., description="Unique patient identifier, e.g. P0000001")
    age: int = Field(..., ge=18, le=100, description="Age in years")
    sex: Sex = Field(..., description="0 = female, 1 = male")
    chest_pain_type: ChestPainType = Field(..., description="cp: 0-3")
    resting_bp: int = Field(..., ge=80, le=220, description="Resting systolic BP (mm Hg)")
    cholesterol: int = Field(..., ge=100, le=600, description="Serum cholesterol (mg/dl)")
    fasting_blood_sugar: FastingBloodSugar = Field(..., description="fbs > 120 mg/dl")
    rest_ecg: RestECG = Field(..., description="restecg: 0-2")
    max_heart_rate: int = Field(..., ge=60, le=220, description="Max heart rate achieved (thalach)")
    exercise_angina: ExerciseAngina = Field(..., description="exang: 0/1")
    st_depression: float = Field(..., ge=0.0, le=7.0, description="ST depression (oldpeak)")
    st_slope: STSlope = Field(..., description="slope: 0-2")
    ca_vessels: int = Field(..., ge=0, le=3, description="# major vessels coloured by fluoroscopy")
    thal: Thalassemia = Field(..., description="thal: 1/2/3")
    target: int = Field(..., ge=0, le=1, description="1 = heart disease present, 0 = absent")
    risk_level: RiskLevel = Field(..., description="Derived Low/Medium/High risk bucket")

    @field_validator("patient_id")
    @classmethod
    def _validate_patient_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("patient_id must be a non-empty string")
        return v.strip()

    model_config = {"use_enum_values": True}


class ClinicalNote(BaseModel):
    """A single unstructured clinical note authored for a patient."""

    note_id: str = Field(..., description="Unique note identifier")
    patient_id: str = Field(..., description="FK -> PatientRecord.patient_id")
    timestamp: datetime = Field(..., description="When the note was authored")
    note_type: NoteType = Field(..., description="admission/progress/discharge/consult/nursing")
    author_role: str = Field(..., description="e.g. Cardiologist, ED Physician, RN")
    note_text: str = Field(..., min_length=1, description="Free-text clinical narrative")

    @field_validator("note_text")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("note_text must not be empty")
        return v

    model_config = {"use_enum_values": True}


class EDVisit(BaseModel):
    """A single Emergency Department visit event."""

    visit_id: str = Field(..., description="Unique visit identifier")
    patient_id: str = Field(..., description="FK -> PatientRecord.patient_id")
    visit_timestamp: datetime = Field(..., description="Arrival timestamp")
    chief_complaint: str = Field(..., min_length=1, description="Presenting complaint")
    triage_level: TriageLevel = Field(..., description="ESI triage level 1-5")
    disposition: Disposition = Field(..., description="Outcome of the visit")
    los_hours: float = Field(..., ge=0.0, le=720.0, description="Length of stay in hours")
    resources_used: int = Field(..., ge=0, le=50, description="Count of resources consumed")

    model_config = {"use_enum_values": True}


class FeatureVector(BaseModel):
    """Fully engineered, model-ready feature vector for one patient.

    Produced by ``feature_engineering.py`` and persisted to the feature store.
    Combines the structured EHR fields with derived risk scores, temporal ED
    aggregates and NLP-derived flags.
    """

    patient_id: str

    # --- structured clinical (numeric) ---
    age: int
    resting_bp: int
    cholesterol: int
    max_heart_rate: int
    st_depression: float
    ca_vessels: int

    # --- structured clinical (categorical, encoded) ---
    sex: int
    chest_pain_type: int
    fasting_blood_sugar: int
    rest_ecg: int
    exercise_angina: int
    st_slope: int
    thal: int

    # --- engineered features ---
    framingham_risk_score: float = Field(..., description="Framingham-like composite 0-100")
    ten_year_cvd_risk_pct: float = Field(..., description="Estimated 10-year CVD risk %")
    age_group: str = Field(..., description="Age bucket, e.g. 30-39")
    comorbidity_index: int = Field(..., description="Count of comorbid risk factors")
    bp_category: str = Field(..., description="Normal/Elevated/Stage1/Stage2/Crisis")
    cholesterol_category: str = Field(..., description="Desirable/Borderline/High")
    hr_reserve_ratio: float = Field(..., description="max_hr / age-predicted max")

    # --- NLP-derived flags from clinical notes ---
    note_count: int = Field(0, description="Number of clinical notes for the patient")
    mentions_chest_pain: int = Field(0, description="1 if any note mentions chest pain")
    mentions_dyspnea: int = Field(0, description="1 if any note mentions dyspnea")
    mentions_diabetes: int = Field(0, description="1 if any note mentions diabetes")
    mentions_smoking: int = Field(0, description="1 if any note mentions smoking")

    # --- temporal ED aggregates ---
    ed_visit_count: int = Field(0, description="Total ED visits in the window")
    ed_avg_los_hours: float = Field(0.0, description="Mean length of stay across visits")
    ed_high_acuity_visits: int = Field(0, description="Visits with triage level <= 2")
    ed_avg_resources: float = Field(0.0, description="Mean resources used per visit")

    # --- targets ---
    target: int
    risk_level: str


# ---------------------------------------------------------------------------
# Column groupings & constants (consumed by other modules)
# ---------------------------------------------------------------------------
PATIENT_COLUMNS: List[str] = list(PatientRecord.model_fields.keys())
CLINICAL_NOTE_COLUMNS: List[str] = list(ClinicalNote.model_fields.keys())
ED_VISIT_COLUMNS: List[str] = list(EDVisit.model_fields.keys())
FEATURE_COLUMNS: List[str] = list(FeatureVector.model_fields.keys())

# Numeric vs categorical partitioning of the structured EHR record. Used by the
# transformer for normalisation / encoding.
NUMERIC_FEATURES: List[str] = [
    "age",
    "resting_bp",
    "cholesterol",
    "max_heart_rate",
    "st_depression",
    "ca_vessels",
]

CATEGORICAL_FEATURES: List[str] = [
    "sex",
    "chest_pain_type",
    "fasting_blood_sugar",
    "rest_ecg",
    "exercise_angina",
    "st_slope",
    "thal",
]

# Human-readable labels for reporting.
RISK_LEVELS: List[str] = [r.value for r in RiskLevel]


__all__ = [
    # enums
    "Sex",
    "ChestPainType",
    "FastingBloodSugar",
    "RestECG",
    "ExerciseAngina",
    "STSlope",
    "Thalassemia",
    "RiskLevel",
    "NoteType",
    "TriageLevel",
    "Disposition",
    # models
    "PatientRecord",
    "ClinicalNote",
    "EDVisit",
    "FeatureVector",
    # constants
    "PATIENT_COLUMNS",
    "CLINICAL_NOTE_COLUMNS",
    "ED_VISIT_COLUMNS",
    "FEATURE_COLUMNS",
    "NUMERIC_FEATURES",
    "CATEGORICAL_FEATURES",
    "RISK_LEVELS",
]
