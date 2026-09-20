"""
Unit tests for the data-engineering layer.

Covers:
* Pydantic schema validation (valid + invalid records).
* ETL transformation steps (patients, clinical notes, ED visits).
* Data-quality checks (completeness, uniqueness, range validation).
* Feature-engineering functions (Framingham scorer + FeatureEngineer helpers).

Run with:  pytest -q
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

# Ensure the project root is importable when pytest is invoked from anywhere.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pydantic import ValidationError  # noqa: E402

from src.data_engineering import schema  # noqa: E402
from src.data_engineering.etl_pipeline import (  # noqa: E402
    DataQualityChecker,
    DataTransformer,
)
from src.data_engineering.feature_engineering import (  # noqa: E402
    FeatureEngineer,
    FraminghamRiskScorer,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _valid_patient_dict() -> dict:
    return dict(
        patient_id="P0000001",
        age=57,
        sex=1,
        chest_pain_type=3,
        resting_bp=140,
        cholesterol=250,
        fasting_blood_sugar=1,
        rest_ecg=2,
        max_heart_rate=130,
        exercise_angina=1,
        st_depression=2.0,
        st_slope=1,
        ca_vessels=1,
        thal=3,
        target=1,
        risk_level="High",
    )


@pytest.fixture
def patients_df() -> pd.DataFrame:
    rows = [
        _valid_patient_dict(),
        dict(
            patient_id="P0000002",
            age=34,
            sex=0,
            chest_pain_type=0,
            resting_bp=110,
            cholesterol=180,
            fasting_blood_sugar=0,
            rest_ecg=0,
            max_heart_rate=175,
            exercise_angina=0,
            st_depression=0.0,
            st_slope=0,
            ca_vessels=0,
            thal=1,
            target=0,
            risk_level="Low",
        ),
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def notes_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            dict(
                note_id="N1",
                patient_id="P0000001",
                timestamp="2026-01-01 10:00:00",
                note_type="admission",
                author_role="ED Physician",
                note_text="Patient reports chest pain and shortness of breath. History of smoking.",
            ),
            dict(
                note_id="N2",
                patient_id="P0000001",
                timestamp="2026-01-02 10:00:00",
                note_type="progress",
                author_role="RN",
                note_text="Stable overnight. No further angina episodes.",
            ),
            dict(
                note_id="N3",
                patient_id="P0000002",
                timestamp="2026-01-03 10:00:00",
                note_type="discharge",
                author_role="Cardiologist",
                note_text="Routine review, no acute complaints.",
            ),
        ]
    )


@pytest.fixture
def ed_visits_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            dict(
                visit_id="V1",
                patient_id="P0000001",
                visit_timestamp="2026-01-01 09:30:00",
                chief_complaint="chest pain",
                triage_level=2,
                disposition="admitted",
                los_hours=12.5,
                resources_used=8,
            ),
            dict(
                visit_id="V2",
                patient_id="P0000002",
                visit_timestamp="2026-07-15 22:15:00",
                chief_complaint="minor laceration",
                triage_level=4,
                disposition="discharged",
                los_hours=1.5,
                resources_used=2,
            ),
        ]
    )


# --------------------------------------------------------------------------- #
# 1. Schema validation
# --------------------------------------------------------------------------- #
def test_patient_record_valid():
    rec = schema.PatientRecord(**_valid_patient_dict())
    assert rec.patient_id == "P0000001"
    assert rec.age == 57
    # use_enum_values=True => stored as raw int/str
    assert rec.risk_level == "High"


def test_patient_record_rejects_out_of_range_age():
    bad = _valid_patient_dict()
    bad["age"] = 5  # below ge=18
    with pytest.raises(ValidationError):
        schema.PatientRecord(**bad)


def test_patient_record_rejects_blank_id():
    bad = _valid_patient_dict()
    bad["patient_id"] = "   "
    with pytest.raises(ValidationError):
        schema.PatientRecord(**bad)


def test_clinical_note_rejects_empty_text():
    with pytest.raises(ValidationError):
        schema.ClinicalNote(
            note_id="N9",
            patient_id="P0000001",
            timestamp="2026-01-01 10:00:00",
            note_type="admission",
            author_role="RN",
            note_text="   ",
        )


def test_ed_visit_valid_and_los_bounds():
    visit = schema.EDVisit(
        visit_id="V1",
        patient_id="P0000001",
        visit_timestamp="2026-01-01 09:30:00",
        chief_complaint="chest pain",
        triage_level=2,
        disposition="admitted",
        los_hours=12.5,
        resources_used=8,
    )
    assert visit.triage_level == 2
    with pytest.raises(ValidationError):
        schema.EDVisit(
            visit_id="V2",
            patient_id="P0000001",
            visit_timestamp="2026-01-01 09:30:00",
            chief_complaint="x",
            triage_level=2,
            disposition="admitted",
            los_hours=999.0,  # exceeds le=720
            resources_used=8,
        )


def test_schema_column_constants():
    assert "patient_id" in schema.PATIENT_COLUMNS
    assert "visit_id" in schema.ED_VISIT_COLUMNS
    assert schema.RISK_LEVELS == ["Low", "Medium", "High"]
    assert set(schema.NUMERIC_FEATURES).issubset(set(schema.PATIENT_COLUMNS))


# --------------------------------------------------------------------------- #
# 2. ETL transformation steps
# --------------------------------------------------------------------------- #
def test_transform_patients_adds_zscores_and_ohe(patients_df):
    out = DataTransformer().transform_patients(patients_df)
    # z-score columns created for numeric features
    for col in schema.NUMERIC_FEATURES:
        assert f"{col}_z" in out.columns
    # one-hot columns created for key categoricals
    assert any(c.startswith("cp_") for c in out.columns)
    assert any(c.startswith("thal_") for c in out.columns)
    assert len(out) == len(patients_df)


def test_transform_patients_imputes_missing_numeric():
    df = pd.DataFrame(
        [_valid_patient_dict(), _valid_patient_dict()]
    )
    df.loc[0, "cholesterol"] = None
    out = DataTransformer().transform_patients(df)
    assert out["cholesterol"].isna().sum() == 0


def test_transform_clinical_notes_extracts_flags(notes_df):
    agg = DataTransformer().transform_clinical_notes(notes_df)
    p1 = agg.loc[agg["patient_id"] == "P0000001"].iloc[0]
    assert p1["mentions_chest_pain"] == 1
    assert p1["mentions_dyspnea"] == 1
    assert p1["mentions_smoking"] == 1
    assert p1["note_count"] == 2
    # patient 2 has no risk keywords
    p2 = agg.loc[agg["patient_id"] == "P0000002"].iloc[0]
    assert p2["mentions_chest_pain"] == 0


def test_transform_ed_visits_adds_temporal_columns(ed_visits_df):
    out = DataTransformer().transform_ed_visits(ed_visits_df)
    for col in ["hour", "day_of_week", "is_weekend", "season", "high_acuity"]:
        assert col in out.columns
    # triage level 2 -> high acuity
    v1 = out.loc[out["visit_id"] == "V1"].iloc[0]
    assert v1["high_acuity"] == 1
    assert v1["admitted_flag"] == 1


# --------------------------------------------------------------------------- #
# 3. Data-quality checks
# --------------------------------------------------------------------------- #
def test_quality_completeness_full(patients_df):
    comp = DataQualityChecker().completeness(patients_df)
    assert all(v == 1.0 for v in comp.values())


def test_quality_uniqueness_detects_duplicates(patients_df):
    dup_df = pd.concat([patients_df, patients_df.iloc[[0]]], ignore_index=True)
    res = DataQualityChecker().uniqueness(dup_df, "patient_id")
    assert res["present"] is True
    assert res["n_duplicates"] == 1
    assert res["is_unique"] is False


def test_quality_range_validation_flags_out_of_range(patients_df):
    bad = patients_df.copy()
    bad.loc[0, "age"] = 250  # out of expected (18, 100)
    report = DataQualityChecker().range_validation(bad)
    assert report["age"]["passed"] is False
    assert report["age"]["out_of_range"] >= 1


# --------------------------------------------------------------------------- #
# 4. Feature engineering
# --------------------------------------------------------------------------- #
def test_framingham_points_monotonic_in_cholesterol():
    assert FraminghamRiskScorer._chol_points(150) < FraminghamRiskScorer._chol_points(300)
    assert FraminghamRiskScorer._bp_points(110) < FraminghamRiskScorer._bp_points(170)


def test_framingham_score_row_and_risk_pct():
    row = pd.Series(_valid_patient_dict())
    row["mentions_smoking"] = 1
    pts = FraminghamRiskScorer.score_row(row)
    assert pts > 0
    risk_pct = FraminghamRiskScorer.points_to_risk_pct(pts)
    assert 0.0 <= risk_pct <= 100.0


def test_framingham_risk_pct_increases_with_points():
    low = FraminghamRiskScorer.points_to_risk_pct(2)
    high = FraminghamRiskScorer.points_to_risk_pct(18)
    assert high > low


def test_feature_engineer_bucketing_helpers():
    assert FeatureEngineer._age_group(35) == "30-39"
    assert FeatureEngineer._age_group(72) == "70+"
    assert FeatureEngineer._bp_category(115) == "Normal"
    assert FeatureEngineer._bp_category(200) == "Crisis"
    assert FeatureEngineer._chol_category(190) == "Desirable"
    assert FeatureEngineer._chol_category(260) == "High"


def test_feature_engineer_comorbidity_index():
    fe = FeatureEngineer()
    high_row = pd.Series(
        dict(resting_bp=150, cholesterol=260, fasting_blood_sugar=1,
             exercise_angina=1, ca_vessels=2, mentions_smoking=1, st_depression=2.5)
    )
    low_row = pd.Series(
        dict(resting_bp=110, cholesterol=180, fasting_blood_sugar=0,
             exercise_angina=0, ca_vessels=0, mentions_smoking=0, st_depression=0.0)
    )
    assert fe._comorbidity_index(high_row) > fe._comorbidity_index(low_row)
    assert fe._comorbidity_index(low_row) == 0


def test_build_patient_features_adds_expected_columns(patients_df):
    out = FeatureEngineer().build_patient_features(patients_df)
    for col in [
        "age_group", "bp_category", "cholesterol_category",
        "comorbidity_index", "hr_reserve_ratio",
        "framingham_risk_score", "ten_year_cvd_risk_pct",
    ]:
        assert col in out.columns
    assert len(out) == len(patients_df)
    assert (out["ten_year_cvd_risk_pct"] >= 0).all()
