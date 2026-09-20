"""
Synthetic multi-source Type 2 Diabetes data generator.

Produces five interlinked raw datasets that mimic a real outpatient diabetes
service, written to ``data/raw/``:

  1. patients.csv           - demographic master (EHR registry)
  2. labs.csv               - laboratory panels
  3. outpatient_visits.csv  - outpatient encounters (24 months, seasonal)
  4. clinical_notes.csv     - unstructured clinical text
  5. complications.csv      - complication label + 12-month deterioration flag

The generator uses clinically-plausible distributions (Pima-style glucose/BMI
ranges, HbA1c, eGFR, ACR) and a latent-risk logistic model that links patient
physiology to both complication type and the deterioration outcome, so the
downstream ML task is learnable but not trivial. No real PHI is used.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.utils.common import get_logger, get_paths, load_config, set_global_seed

logger = get_logger("data_generator")

REGIONS = ["North", "South", "East", "West", "Central"]
CLINICS = ["endocrinology", "primary_care", "cardiology", "nephrology", "ophthalmology"]
VISIT_TYPES = ["routine", "follow_up", "urgent", "telehealth"]
NOTE_TEMPLATES = {
    "well_controlled": [
        "Patient reports good adherence to metformin. HbA1c stable. No new symptoms.",
        "Glycemic control adequate. Continue current regimen. Diet and exercise reinforced.",
        "Routine follow-up. Feet examined, no ulcers. Vision unchanged.",
    ],
    "poorly_controlled": [
        "Persistent hyperglycemia despite therapy. Reports polyuria and fatigue.",
        "HbA1c rising. Missed several doses. Considering insulin intensification.",
        "Complains of blurred vision and tingling in feet. Referral placed.",
    ],
    "complication": [
        "Microalbuminuria noted, eGFR declining. Nephrology referral initiated.",
        "Non-proliferative retinopathy on fundus exam. Ophthalmology follow-up.",
        "Reduced monofilament sensation bilaterally, consistent with neuropathy.",
        "Chest discomfort on exertion. ECG ordered, cardiology consult requested.",
    ],
}


def _latent_risk(age, hba1c, years, egfr, systolic, ldl, bmi, smoker):
    """Latent linear predictor driving complication & deterioration probability."""
    z = (
        -1.35
        + 0.045 * (age - 55)
        + 0.55 * (hba1c - 7.0)
        + 0.06 * years
        + 0.020 * (systolic - 130)
        + 0.006 * (ldl - 100)
        + 0.04 * (bmi - 28)
        - 0.025 * (egfr - 90)
        + 0.5 * smoker
    )
    return z


def generate(n_patients: int | None = None, seed: int | None = None) -> dict:
    """Generate all raw datasets and persist them to ``data/raw``."""
    cfg = load_config()
    seed = set_global_seed(seed)
    rng = np.random.default_rng(seed)
    paths = get_paths()

    dg = cfg["data_generation"]
    n = n_patients or dg["n_patients"]
    months = dg["months_of_visits"]
    logger.info("Generating synthetic data for %d patients over %d months", n, months)

    # ---------------- Demographics ----------------
    ages = np.clip(rng.normal(58, 12, n), 18, 95).astype(int)
    sex = rng.choice(["M", "F"], n, p=[0.52, 0.48])
    bmi = np.clip(rng.normal(30.5, 5.5, n), 15, 65).round(1)
    smoking = rng.choice(["never", "former", "current"], n, p=[0.5, 0.32, 0.18])
    years_dx = np.clip(rng.gamma(3.0, 2.5, n), 0, 45).round(1)
    fam_hist = rng.random(n) < 0.42
    insurance = rng.choice(["public", "private", "uninsured"], n, p=[0.48, 0.42, 0.10])
    region = rng.choice(REGIONS, n)
    patient_ids = [f"PT{100000 + i}" for i in range(n)]

    patients = pd.DataFrame({
        "patient_id": patient_ids,
        "age": ages,
        "sex": sex,
        "bmi": bmi,
        "smoking_status": smoking,
        "years_since_diagnosis": years_dx,
        "family_history_diabetes": fam_hist,
        "insurance_type": insurance,
        "region": region,
    })

    # ---------------- Labs ----------------
    hba1c = np.clip(rng.normal(7.6, 1.5, n) + 0.02 * (years_dx), 4.2, 17).round(1)
    fasting_glucose = np.clip(rng.normal(140, 45, n) + 6 * (hba1c - 7), 55, 480).round(0)
    systolic = np.clip(rng.normal(134, 16, n) + 0.15 * (ages - 55), 85, 235).astype(int)
    diastolic = np.clip(rng.normal(82, 10, n), 45, 135).astype(int)
    ldl = np.clip(rng.normal(112, 32, n), 35, 280).round(0)
    hdl = np.clip(rng.normal(46, 12, n), 18, 110).round(0)
    trig = np.clip(rng.normal(165, 70, n), 40, 750).round(0)
    creatinine = np.clip(rng.normal(1.0, 0.35, n) + 0.01 * (ages - 55), 0.4, 10).round(2)
    egfr = np.clip(140 - 0.9 * (ages - 40) - 20 * (creatinine - 1.0) + rng.normal(0, 8, n), 6, 145).round(0)
    urine_acr = np.clip(rng.gamma(1.3, 25, n) * (1 + 0.02 * (hba1c - 7)), 0, 2800).round(1)

    base = date.today() - timedelta(days=months * 30)
    lab_dates = [base + timedelta(days=int(rng.integers(0, months * 30))) for _ in range(n)]
    labs = pd.DataFrame({
        "patient_id": patient_ids,
        "sample_date": lab_dates,
        "hba1c": hba1c,
        "fasting_glucose": fasting_glucose,
        "systolic_bp": systolic,
        "diastolic_bp": diastolic,
        "ldl": ldl,
        "hdl": hdl,
        "triglycerides": trig,
        "creatinine": creatinine,
        "egfr": egfr,
        "urine_acr": urine_acr,
    })

    # ---------------- Complications & deterioration (latent model) ----------------
    smoker_flag = (smoking == "current").astype(float)
    z = _latent_risk(ages, hba1c, years_dx, egfr, systolic, ldl, bmi, smoker_flag)
    p_any = 1 / (1 + np.exp(-z))  # probability of having ANY complication

    prev = dg["complication_prevalence"]
    comp_types = ["retinopathy", "nephropathy", "neuropathy", "cardiovascular"]
    # organ-specific propensity weights modulated by physiology
    w_retino = np.clip(0.5 + 0.3 * (hba1c - 7) / 3, 0.1, 3)
    w_nephro = np.clip(0.5 + 0.5 * (90 - egfr) / 40 + 0.3 * (urine_acr / 300), 0.1, 3)
    w_neuro = np.clip(0.5 + 0.25 * (years_dx / 10) + 0.2 * (hba1c - 7) / 3, 0.1, 3)
    w_cardio = np.clip(0.4 + 0.02 * (systolic - 130) + 0.004 * (ldl - 100) + 0.4 * smoker_flag, 0.1, 3)
    wmat = np.vstack([w_retino, w_nephro, w_neuro, w_cardio]).T
    wmat = wmat / wmat.sum(axis=1, keepdims=True)

    has_comp = rng.random(n) < p_any
    complication = np.array(["none"] * n, dtype=object)
    for i in range(n):
        if has_comp[i]:
            complication[i] = rng.choice(comp_types, p=wmat[i])

    # 12-month deterioration: higher for those with complications / high latent risk
    p_det = 1 / (1 + np.exp(-(z - 1.1 + 0.9 * has_comp + rng.normal(0, 0.3, n))))
    deteriorated = (rng.random(n) < p_det).astype(int)

    complications = pd.DataFrame({
        "patient_id": patient_ids,
        "complication": complication,
        "deteriorated_12m": deteriorated.astype(bool),
    })

    # ---------------- Outpatient visits (24 months, seasonal + weekday) ----------------
    visits = _generate_visits(patient_ids, complication, deteriorated, months, base, rng)

    # ---------------- Clinical notes ----------------
    notes = _generate_notes(patient_ids, hba1c, complication, base, months, rng,
                            dg["clinical_notes_per_patient"])

    # ---------------- Persist ----------------
    raw = paths["data_raw"]
    patients.to_csv(raw / "patients.csv", index=False)
    labs.to_csv(raw / "labs.csv", index=False)
    visits.to_csv(raw / "outpatient_visits.csv", index=False)
    notes.to_csv(raw / "clinical_notes.csv", index=False)
    complications.to_csv(raw / "complications.csv", index=False)

    logger.info("Wrote raw datasets to %s", raw)
    logger.info("  patients=%d labs=%d visits=%d notes=%d complications=%d",
                len(patients), len(labs), len(visits), len(notes), len(complications))
    logger.info("  complication prevalence: %s",
                complications["complication"].value_counts(normalize=True).round(3).to_dict())
    logger.info("  deterioration rate: %.3f", complications["deteriorated_12m"].mean())

    return {
        "patients": patients,
        "labs": labs,
        "outpatient_visits": visits,
        "clinical_notes": notes,
        "complications": complications,
    }


def _generate_visits(patient_ids, complication, deteriorated, months, base, rng):
    """Create outpatient encounters with seasonal + weekday arrival structure."""
    n = len(patient_ids)
    rows = []
    enc = 0
    # sicker patients visit more often
    intensity = 3 + 2 * (complication != "none").astype(int) + 2 * deteriorated
    for i in range(n):
        n_visits = rng.poisson(intensity[i] * months / 12)
        n_visits = max(1, n_visits)
        for _ in range(n_visits):
            day_offset = int(rng.integers(0, months * 30))
            vdate = base + timedelta(days=day_offset)
            # winter uplift (flu/cardio) — seasonal multiplier via rejection sampling
            month = vdate.month
            season_boost = 1.25 if month in (11, 12, 1, 2) else (0.9 if month in (6, 7, 8) else 1.0)
            if rng.random() > season_boost / 1.25:
                continue
            # avoid weekends for most visits
            while vdate.weekday() >= 5 and rng.random() < 0.85:
                vdate += timedelta(days=1)
            if complication[i] == "cardiovascular":
                clinic = rng.choice(CLINICS, p=[0.25, 0.25, 0.3, 0.1, 0.1])
            elif complication[i] == "nephropathy":
                clinic = rng.choice(CLINICS, p=[0.25, 0.25, 0.05, 0.35, 0.1])
            elif complication[i] == "retinopathy":
                clinic = rng.choice(CLINICS, p=[0.25, 0.25, 0.05, 0.05, 0.4])
            else:
                clinic = rng.choice(CLINICS, p=[0.4, 0.4, 0.07, 0.06, 0.07])
            vtype = rng.choice(VISIT_TYPES, p=[0.35, 0.4, 0.12, 0.13])
            duration = int(np.clip(rng.normal(28, 10), 5, 120))
            no_show = rng.random() < 0.08
            rows.append((f"ENC{500000 + enc}", patient_ids[i], vdate, clinic, vtype, duration, no_show))
            enc += 1
    visits = pd.DataFrame(rows, columns=[
        "encounter_id", "patient_id", "visit_date", "clinic", "visit_type", "duration_min", "no_show"
    ])
    return visits.sort_values("visit_date").reset_index(drop=True)


def _generate_notes(patient_ids, hba1c, complication, base, months, rng, per_patient):
    rows = []
    for i, pid in enumerate(patient_ids):
        for _ in range(max(1, rng.poisson(per_patient))):
            if complication[i] != "none":
                text = rng.choice(NOTE_TEMPLATES["complication"])
                ntype = "consult"
            elif hba1c[i] >= 8.0:
                text = rng.choice(NOTE_TEMPLATES["poorly_controlled"])
                ntype = "progress"
            else:
                text = rng.choice(NOTE_TEMPLATES["well_controlled"])
                ntype = "progress"
            ndate = base + timedelta(days=int(rng.integers(0, months * 30)))
            rows.append((pid, ndate, ntype, text))
    return pd.DataFrame(rows, columns=["patient_id", "note_date", "note_type", "note_text"])


if __name__ == "__main__":
    generate()
