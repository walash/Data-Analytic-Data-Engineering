"""
Test suite for the data-engineering, schema and forecasting layers.

The tests are lightweight and deterministic: the heavy data generator is run
once at a small scale in a session-scoped fixture, and the remaining tests
validate schema behaviour, config integrity and the forecasting utilities.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.data_engineering import data_generator
from src.data_engineering.schema import (
    ComplicationOutcome,
    LabResult,
    OutpatientEncounter,
    PatientDemographics,
)
from src.forecasting.resource_scheduler import ResourceScheduler
from src.forecasting.schedule_optimizer import ScheduleOptimizer
from src.utils.common import COMPLICATION_CLASSES, load_config


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def raw_frames():
    """Generate a small synthetic dataset once for the module."""
    return data_generator.generate(n_patients=300, seed=7)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def test_config_has_required_sections():
    cfg = load_config()
    for section in ("project", "data_generation", "etl", "modeling", "forecasting", "scheduling"):
        assert section in cfg, f"missing config section: {section}"


def test_scheduling_config_values():
    sch = load_config()["scheduling"]
    assert sch["visits_per_physician_per_week"] > 0
    assert 0 <= sch["surge_buffer"] < 1
    assert 0 < sch["high_load_utilization_threshold"] <= 1
    for role in ("physician", "nurse", "admin"):
        assert sch["cost_per_week"][role] > 0


# --------------------------------------------------------------------------- #
# Data generation
# --------------------------------------------------------------------------- #
def test_generate_returns_all_tables(raw_frames):
    for key in ("patients", "labs", "outpatient_visits", "clinical_notes", "complications"):
        assert key in raw_frames
        assert isinstance(raw_frames[key], pd.DataFrame)
        assert len(raw_frames[key]) > 0


def test_generate_is_deterministic():
    a = data_generator.generate(n_patients=120, seed=123)
    b = data_generator.generate(n_patients=120, seed=123)
    pd.testing.assert_frame_equal(a["patients"], b["patients"])


def test_patient_ids_unique(raw_frames):
    pid = raw_frames["patients"]["patient_id"]
    assert pid.is_unique


def test_complication_classes_valid(raw_frames):
    comp = raw_frames["complications"]["complication"].unique()
    assert set(comp).issubset(set(COMPLICATION_CLASSES))


def test_labs_reference_existing_patients(raw_frames):
    pids = set(raw_frames["patients"]["patient_id"])
    assert set(raw_frames["labs"]["patient_id"]).issubset(pids)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
def _valid_patient(**overrides):
    base = dict(
        patient_id="P0001", age=55, sex="F", bmi=28.4,
        smoking_status="never", years_since_diagnosis=6.0,
        family_history_diabetes=True, insurance_type="private", region="North")
    base.update(overrides)
    return PatientDemographics(**base)


def test_patient_schema_accepts_valid():
    p = _valid_patient()
    assert p.age == 55


def test_patient_schema_rejects_bad_age():
    with pytest.raises(Exception):
        _valid_patient(age=-3)


def test_lab_schema_types():
    lab = LabResult(
        patient_id="P0001", sample_date="2024-01-10", hba1c=7.2,
        fasting_glucose=140, systolic_bp=130, diastolic_bp=80, ldl=110.0,
        hdl=45.0, triglycerides=160.0, creatinine=1.0, egfr=88.0, urine_acr=20.0)
    assert isinstance(lab.hba1c, float)


def test_encounter_schema():
    enc = OutpatientEncounter(
        encounter_id="E0001", patient_id="P0001", visit_date="2024-01-10",
        clinic="endocrinology", visit_type="routine", duration_min=25)
    assert enc.clinic == "endocrinology"


def test_complication_outcome_schema():
    out = ComplicationOutcome(
        patient_id="P0001", complication="neuropathy", deteriorated_12m=True)
    assert out.deteriorated_12m is True


# --------------------------------------------------------------------------- #
# Forecasting / scheduling logic
# --------------------------------------------------------------------------- #
def test_resource_scheduler_math(tmp_path, monkeypatch):
    """Staffing must cover surge-buffered demand and flag utilisation correctly."""
    sched = ResourceScheduler()
    df = pd.DataFrame([{"forecast": 800.0}])
    # monkeypatch loader to avoid dependence on generated csv
    monkeypatch.setattr(sched, "_load_forecast",
                        lambda: df.assign(week="2026-01-05", series="total",
                                          lower=0, upper=0, method="test"))
    out = sched.build_schedule()
    row = out.iloc[0]
    planned = 800.0 * (1 + sched.buffer)
    assert row["physicians"] * sched.vpp >= planned - 1e-6
    assert 0 < row["projected_utilization"] <= 1.0


def test_schedule_optimizer_meets_demand(monkeypatch):
    opt = ScheduleOptimizer()
    df = pd.DataFrame([{"forecast": 600.0}])
    monkeypatch.setattr(opt, "_load_forecast",
                        lambda: df.assign(week="2026-01-05", series="total",
                                          lower=0, upper=0, method="test"))
    res = opt.optimize().iloc[0]
    required = 600.0 * (1 + opt.buffer)
    assert res["physicians"] * opt.vpp >= required - 1e-6
    assert res["nurses"] * opt.vpn >= required - 1e-6
    assert res["weekly_cost"] > 0
