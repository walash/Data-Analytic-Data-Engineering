"""
data_generator.py
=================

Synthetic data generator for the CVD Risk Stratification & ED Capacity Planning
project. Produces three raw datasets that mimic a real hospital data warehouse
*without any real PHI*:

1. ``data/raw/patients.csv``        - 10,000 structured EHR records whose
   distributions are modelled on the UCI Heart Disease (Cleveland) dataset.
2. ``data/raw/clinical_notes.csv``  - unstructured free-text clinical notes,
   1-4 per patient, correlated with the patient's clinical profile.
3. ``data/raw/ed_visits.csv``       - 24 months of Emergency Department visits
   with realistic seasonal, day-of-week and time-of-day arrival patterns.

Design goals
------------
* **Statistically plausible** - marginal distributions and inter-feature
  correlations (e.g. age <-> BP, cholesterol <-> disease) are baked in.
* **Reproducible** - a fixed RNG seed yields identical data every run.
* **Self-contained** - depends only on numpy, pandas, scipy and faker.

Run directly::

    python -m src.data_engineering.data_generator --patients 10000 --seed 42
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from faker import Faker
from scipy import stats

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("data_generator")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
RAW_DIR = os.path.join(PROJECT_ROOT, "data", "raw")

DEFAULT_N_PATIENTS = 10_000
DEFAULT_SEED = 42
ED_HISTORY_MONTHS = 24


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------
class SyntheticDataGenerator:
    """Generate correlated synthetic patient, clinical-note and ED-visit data."""

    def __init__(self, n_patients: int = DEFAULT_N_PATIENTS, seed: int = DEFAULT_SEED):
        self.n_patients = n_patients
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.faker = Faker()
        Faker.seed(seed)
        self._reference_end = datetime(2026, 9, 1, 0, 0, 0)
        self._reference_start = self._reference_end - timedelta(days=ED_HISTORY_MONTHS * 30)

    # ------------------------------------------------------------------ #
    # 1. Structured patient records
    # ------------------------------------------------------------------ #
    def generate_patients(self) -> pd.DataFrame:
        """Generate the structured EHR table with UCI-like distributions."""
        n = self.n_patients
        rng = self.rng
        logger.info("Generating %d patient records ...", n)

        # Age: truncated normal centred ~54 (UCI mean), clipped to [30, 80].
        age = stats.truncnorm.rvs(
            (30 - 54) / 9, (80 - 54) / 9, loc=54, scale=9, size=n, random_state=self.seed
        ).round().astype(int)

        # Sex: ~68% male in UCI.
        sex = rng.choice([0, 1], size=n, p=[0.32, 0.68])

        # Chest pain type distribution (UCI marginals, 0-3).
        chest_pain_type = rng.choice([0, 1, 2, 3], size=n, p=[0.47, 0.17, 0.29, 0.07])

        # Resting BP increases mildly with age.
        resting_bp = (
            120
            + 0.35 * (age - 54)
            + rng.normal(0, 15, n)
        ).clip(90, 200).round().astype(int)

        # Cholesterol: age-correlated, right-skewed.
        cholesterol = (
            200
            + 0.6 * (age - 54)
            + rng.gamma(shape=2.0, scale=18.0, size=n)
            - 30
        ).clip(120, 560).round().astype(int)

        # Fasting blood sugar > 120 mg/dl: ~15% prevalence, higher with age.
        fbs_prob = np.clip(0.10 + 0.004 * (age - 40), 0.05, 0.40)
        fasting_blood_sugar = (rng.random(n) < fbs_prob).astype(int)

        # Resting ECG.
        rest_ecg = rng.choice([0, 1, 2], size=n, p=[0.49, 0.03, 0.48])

        # Max heart rate: decreases with age (~220 - age with noise).
        max_heart_rate = (
            (220 - age) * 0.85
            + rng.normal(0, 18, n)
        ).clip(70, 210).round().astype(int)

        # Exercise-induced angina: ~33% prevalence.
        exercise_angina = rng.choice([0, 1], size=n, p=[0.67, 0.33])

        # ST depression (oldpeak): exponential-ish, mostly small.
        st_depression = np.round(rng.gamma(shape=1.3, scale=0.8, size=n).clip(0, 6.5), 1)

        # ST slope.
        st_slope = rng.choice([0, 1, 2], size=n, p=[0.21, 0.46, 0.33])

        # Number of major vessels (ca).
        ca_vessels = rng.choice([0, 1, 2, 3], size=n, p=[0.58, 0.22, 0.13, 0.07])

        # Thalassemia.
        thal = rng.choice([1, 2, 3], size=n, p=[0.55, 0.06, 0.39])

        # ------------------------------------------------------------------
        # Disease target via a latent logistic risk model of the features.
        # ------------------------------------------------------------------
        z = (
            0.030 * (age - 54)
            + 0.55 * sex
            + 0.45 * (chest_pain_type == 3)
            + 0.020 * (resting_bp - 130)
            + 0.006 * (cholesterol - 240)
            + 0.35 * fasting_blood_sugar
            + 0.30 * (rest_ecg == 2)
            - 0.030 * (max_heart_rate - 150)
            + 0.85 * exercise_angina
            + 0.55 * st_depression
            + 0.40 * (st_slope == 1)
            + 0.60 * ca_vessels
            + 0.70 * (thal == 3)
            - 3.60
        )
        prob_disease = 1.0 / (1.0 + np.exp(-z))
        target = (rng.random(n) < prob_disease).astype(int)

        # Continuous risk score -> Low / Medium / High via tertile-like cuts.
        risk_score = prob_disease
        risk_level = np.where(
            risk_score < 0.33, "Low",
            np.where(risk_score < 0.60, "Medium", "High"),
        )

        patient_id = [f"P{idx:07d}" for idx in range(1, n + 1)]

        df = pd.DataFrame(
            {
                "patient_id": patient_id,
                "age": age,
                "sex": sex,
                "chest_pain_type": chest_pain_type,
                "resting_bp": resting_bp,
                "cholesterol": cholesterol,
                "fasting_blood_sugar": fasting_blood_sugar,
                "rest_ecg": rest_ecg,
                "max_heart_rate": max_heart_rate,
                "exercise_angina": exercise_angina,
                "st_depression": st_depression,
                "st_slope": st_slope,
                "ca_vessels": ca_vessels,
                "thal": thal,
                "target": target,
                "risk_level": risk_level,
            }
        )
        logger.info(
            "Patient disease prevalence: %.1f%% | risk mix L/M/H: %s",
            100 * df["target"].mean(),
            df["risk_level"].value_counts(normalize=True).round(3).to_dict(),
        )
        return df

    # ------------------------------------------------------------------ #
    # 2. Unstructured clinical notes
    # ------------------------------------------------------------------ #
    _COMPLAINTS_BY_RISK: Dict[str, List[str]] = {
        "High": [
            "crushing substernal chest pain radiating to the left arm",
            "acute shortness of breath with diaphoresis",
            "exertional angina progressing at rest",
            "palpitations with presyncope",
        ],
        "Medium": [
            "intermittent chest tightness on exertion",
            "mild dyspnea on climbing stairs",
            "atypical chest discomfort",
            "fatigue and occasional palpitations",
        ],
        "Low": [
            "routine cardiovascular follow-up, asymptomatic",
            "no acute complaints, medication review",
            "mild non-cardiac chest wall soreness",
            "annual wellness visit",
        ],
    }

    _ROLES = ["Cardiologist", "ED Physician", "Internal Medicine", "RN", "Nurse Practitioner"]

    def _compose_note(self, row: pd.Series, note_type: str) -> str:
        """Build a plausible free-text narrative from the structured record."""
        rng = self.rng
        risk = row["risk_level"]
        complaint = rng.choice(self._COMPLAINTS_BY_RISK[risk])
        sex_word = "male" if row["sex"] == 1 else "female"

        segments = [
            f"{int(row['age'])}-year-old {sex_word} presenting with {complaint}.",
            f"Resting BP {int(row['resting_bp'])} mmHg, cholesterol {int(row['cholesterol'])} mg/dl, "
            f"max HR {int(row['max_heart_rate'])} bpm.",
        ]

        if row["fasting_blood_sugar"] == 1:
            segments.append("History of diabetes mellitus with elevated fasting glucose.")
        if row["exercise_angina"] == 1:
            segments.append("Reports exercise-induced angina.")
        if row["st_depression"] >= 2.0:
            segments.append(
                f"Stress test shows ST depression of {row['st_depression']:.1f} mm."
            )
        if row["ca_vessels"] >= 1:
            segments.append(
                f"Angiography reveals {int(row['ca_vessels'])} diseased major vessel(s)."
            )
        # Inject a smoking mention stochastically (not in structured data).
        if rng.random() < (0.45 if risk == "High" else 0.20):
            segments.append("Patient is a current smoker; counselled on cessation.")

        if note_type == "discharge":
            segments.append(
                "Discharged on aspirin, statin and beta-blocker with cardiology follow-up."
            )
        elif note_type == "admission":
            segments.append("Admitted for further cardiac monitoring and workup.")
        elif note_type == "progress":
            segments.append("Condition stable; continuing current management plan.")
        elif note_type == "consult":
            segments.append("Cardiology consult requested for risk stratification.")
        else:  # nursing
            segments.append("Vitals monitored q4h; patient tolerating oral intake.")

        return " ".join(segments)

    def generate_clinical_notes(self, patients: pd.DataFrame) -> pd.DataFrame:
        """Generate 1-4 clinical notes per patient correlated with their profile."""
        rng = self.rng
        logger.info("Generating clinical notes ...")
        records: List[dict] = []
        note_types = ["admission", "progress", "discharge", "consult", "nursing"]
        note_counter = 0

        for _, row in patients.iterrows():
            # Higher-risk patients accumulate more documentation.
            base = {"High": 3, "Medium": 2, "Low": 1}[row["risk_level"]]
            n_notes = int(np.clip(rng.poisson(base) + 1, 1, 4))
            for _ in range(n_notes):
                note_counter += 1
                note_type = str(rng.choice(note_types, p=[0.25, 0.30, 0.20, 0.10, 0.15]))
                ts = self._reference_start + timedelta(
                    days=int(rng.integers(0, ED_HISTORY_MONTHS * 30)),
                    hours=int(rng.integers(0, 24)),
                    minutes=int(rng.integers(0, 60)),
                )
                records.append(
                    {
                        "note_id": f"N{note_counter:09d}",
                        "patient_id": row["patient_id"],
                        "timestamp": ts.isoformat(sep=" "),
                        "note_type": note_type,
                        "author_role": str(rng.choice(self._ROLES)),
                        "note_text": self._compose_note(row, note_type),
                    }
                )

        df = pd.DataFrame.from_records(records)
        logger.info("Generated %d clinical notes for %d patients.", len(df), len(patients))
        return df

    # ------------------------------------------------------------------ #
    # 3. Emergency Department visits (24 months)
    # ------------------------------------------------------------------ #
    _COMPLAINTS = [
        "Chest pain", "Shortness of breath", "Palpitations", "Syncope",
        "Dizziness", "Abdominal pain", "Hypertensive urgency", "Arrhythmia",
        "Fatigue", "Edema",
    ]

    def _seasonal_weight(self, day: datetime) -> float:
        """Winter months carry higher cardiovascular ED demand."""
        # Peak in Jan (month 1), trough in Jul (month 7).
        month_angle = 2 * np.pi * (day.month - 1) / 12.0
        return 1.0 + 0.25 * np.cos(month_angle)  # ~[0.75, 1.25]

    def _hour_weight(self, hour: int) -> float:
        """Bi-modal arrival curve: late-morning and evening peaks."""
        return (
            0.6
            + 0.9 * np.exp(-((hour - 10) ** 2) / (2 * 3.0**2))
            + 1.0 * np.exp(-((hour - 19) ** 2) / (2 * 3.5**2))
        )

    def generate_ed_visits(self, patients: pd.DataFrame) -> pd.DataFrame:
        """Generate 24 months of ED visits with seasonal / diurnal patterns."""
        rng = self.rng
        logger.info("Generating 24 months of ED visits ...")

        # Expected annual visit rate scales strongly with risk level.
        annual_rate = {"Low": 0.25, "Medium": 0.9, "High": 2.4}
        records: List[dict] = []
        visit_counter = 0

        # Pre-compute per-day seasonal weights over the horizon.
        total_days = ED_HISTORY_MONTHS * 30
        day_list = [self._reference_start + timedelta(days=d) for d in range(total_days)]
        season_w = np.array([self._seasonal_weight(d) for d in day_list])
        # Weekend uplift for ED.
        dow_w = np.array([1.15 if d.weekday() >= 5 else 1.0 for d in day_list])
        day_weights = season_w * dow_w
        day_weights = day_weights / day_weights.sum()

        hour_choices = np.arange(24)
        hour_weights = np.array([self._hour_weight(h) for h in hour_choices])
        hour_weights = hour_weights / hour_weights.sum()

        for _, row in patients.iterrows():
            lam = annual_rate[row["risk_level"]] * (ED_HISTORY_MONTHS / 12.0)
            n_visits = rng.poisson(lam)
            if n_visits == 0:
                continue
            chosen_days = rng.choice(total_days, size=n_visits, p=day_weights)
            for day_idx in chosen_days:
                visit_counter += 1
                base_day = day_list[day_idx]
                hour = int(rng.choice(hour_choices, p=hour_weights))
                minute = int(rng.integers(0, 60))
                ts = base_day.replace(hour=hour, minute=minute, second=0)

                # Acuity correlated with risk level.
                if row["risk_level"] == "High":
                    triage = int(np.clip(rng.choice([1, 2, 3, 4], p=[0.15, 0.40, 0.35, 0.10]), 1, 5))
                elif row["risk_level"] == "Medium":
                    triage = int(rng.choice([2, 3, 4, 5], p=[0.15, 0.45, 0.30, 0.10]))
                else:
                    triage = int(rng.choice([3, 4, 5], p=[0.30, 0.45, 0.25]))

                # Disposition depends on triage acuity.
                if triage == 1:
                    disp = str(rng.choice(["icu_admit", "admitted", "deceased"], p=[0.55, 0.40, 0.05]))
                elif triage == 2:
                    disp = str(rng.choice(["admitted", "icu_admit", "discharged", "transferred"],
                                          p=[0.50, 0.15, 0.30, 0.05]))
                elif triage == 3:
                    disp = str(rng.choice(["discharged", "admitted", "left_without_being_seen"],
                                          p=[0.70, 0.25, 0.05]))
                else:
                    disp = str(rng.choice(["discharged", "left_without_being_seen"], p=[0.90, 0.10]))

                # Length of stay (hours) - lognormal, longer for higher acuity.
                los_mu = {1: 3.2, 2: 2.7, 3: 1.9, 4: 1.2, 5: 0.9}[triage]
                los = float(np.round(np.clip(rng.lognormal(mean=los_mu, sigma=0.5), 0.3, 240.0), 2))

                # Resources scale with acuity.
                res_lambda = {1: 12, 2: 8, 3: 5, 4: 3, 5: 2}[triage]
                resources = int(np.clip(rng.poisson(res_lambda), 0, 40))

                records.append(
                    {
                        "visit_id": f"V{visit_counter:09d}",
                        "patient_id": row["patient_id"],
                        "visit_timestamp": ts.isoformat(sep=" "),
                        "chief_complaint": str(rng.choice(self._COMPLAINTS)),
                        "triage_level": triage,
                        "disposition": disp,
                        "los_hours": los,
                        "resources_used": resources,
                    }
                )

        df = pd.DataFrame.from_records(records)
        df = df.sort_values("visit_timestamp").reset_index(drop=True)
        logger.info(
            "Generated %d ED visits spanning %s to %s.",
            len(df),
            self._reference_start.date(),
            self._reference_end.date(),
        )
        return df

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def generate_all(self, out_dir: str = RAW_DIR) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Generate all three datasets and persist them to ``out_dir``."""
        os.makedirs(out_dir, exist_ok=True)
        patients = self.generate_patients()
        notes = self.generate_clinical_notes(patients)
        visits = self.generate_ed_visits(patients)

        p_path = os.path.join(out_dir, "patients.csv")
        n_path = os.path.join(out_dir, "clinical_notes.csv")
        v_path = os.path.join(out_dir, "ed_visits.csv")

        patients.to_csv(p_path, index=False)
        notes.to_csv(n_path, index=False)
        visits.to_csv(v_path, index=False)

        logger.info("Saved patients      -> %s (%d rows)", p_path, len(patients))
        logger.info("Saved clinical_notes-> %s (%d rows)", n_path, len(notes))
        logger.info("Saved ed_visits     -> %s (%d rows)", v_path, len(visits))
        return patients, notes, visits


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic CVD / ED datasets.")
    parser.add_argument("--patients", type=int, default=DEFAULT_N_PATIENTS,
                        help="Number of patient records to generate.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed.")
    parser.add_argument("--out-dir", type=str, default=RAW_DIR, help="Output directory.")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> None:
    args = _parse_args(argv)
    gen = SyntheticDataGenerator(n_patients=args.patients, seed=args.seed)
    gen.generate_all(out_dir=args.out_dir)


if __name__ == "__main__":
    main()
