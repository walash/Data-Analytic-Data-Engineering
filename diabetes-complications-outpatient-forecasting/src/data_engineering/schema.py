"""
Pydantic v2 data contracts for the multi-source Type 2 Diabetes datasets.

These schemas enforce validity of the raw records the ETL pipeline ingests:
  * PatientDemographics  - registry / EHR demographic master
  * LabResult            - laboratory panels (HbA1c, lipids, renal, etc.)
  * OutpatientEncounter  - outpatient clinic visits (load-forecasting source)
  * ClinicalNote         - unstructured clinical text
  * ComplicationOutcome  - complication labels and 1-year deterioration flag
  * FeatureVector        - final modelling-ready record
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SexT = Literal["M", "F"]
SmokingT = Literal["never", "former", "current"]
ClinicT = Literal["endocrinology", "primary_care", "cardiology", "nephrology", "ophthalmology"]
ComplicationT = Literal["none", "retinopathy", "nephropathy", "neuropathy", "cardiovascular"]


class PatientDemographics(BaseModel):
    patient_id: str
    age: int = Field(ge=18, le=95)
    sex: SexT
    bmi: float = Field(ge=12.0, le=70.0)
    smoking_status: SmokingT
    years_since_diagnosis: float = Field(ge=0, le=50)
    family_history_diabetes: bool
    insurance_type: Literal["public", "private", "uninsured"]
    region: str

    @field_validator("bmi")
    @classmethod
    def _round_bmi(cls, v: float) -> float:
        return round(v, 1)


class LabResult(BaseModel):
    patient_id: str
    sample_date: date
    hba1c: float = Field(ge=4.0, le=18.0)               # %
    fasting_glucose: float = Field(ge=50, le=500)        # mg/dL
    systolic_bp: int = Field(ge=80, le=240)
    diastolic_bp: int = Field(ge=40, le=140)
    ldl: float = Field(ge=30, le=300)                    # mg/dL
    hdl: float = Field(ge=15, le=120)                    # mg/dL
    triglycerides: float = Field(ge=30, le=800)
    creatinine: float = Field(ge=0.3, le=12.0)           # mg/dL
    egfr: float = Field(ge=5, le=150)                    # mL/min/1.73m2
    urine_acr: float = Field(ge=0, le=3000)              # albumin/creatinine ratio


class OutpatientEncounter(BaseModel):
    encounter_id: str
    patient_id: str
    visit_date: date
    clinic: ClinicT
    visit_type: Literal["routine", "follow_up", "urgent", "telehealth"]
    duration_min: int = Field(ge=5, le=180)
    no_show: bool = False


class ClinicalNote(BaseModel):
    patient_id: str
    note_date: date
    note_type: Literal["progress", "consult", "discharge"]
    note_text: str = Field(min_length=1)


class ComplicationOutcome(BaseModel):
    patient_id: str
    complication: ComplicationT
    deteriorated_12m: bool  # new/worsening complication within 12 months (model target)


class FeatureVector(BaseModel):
    """Modelling-ready wide record (subset of engineered columns validated)."""

    patient_id: str
    age: int
    sex: SexT
    bmi: float
    hba1c: float
    fasting_glucose: float
    systolic_bp: int
    ldl: float
    egfr: float
    urine_acr: float
    years_since_diagnosis: float
    comorbidity_index: float
    visit_count_12m: int
    complication: ComplicationT
    deteriorated_12m: int = Field(ge=0, le=1)
