# Outpatient Clinic Load Forecast & Resource Plan

_Generated: 2026-09-20 14:37 | synthetic data_

## 1. Executive summary

- **Forecast model:** ensemble(prophet+sarima)
- **Horizon:** 12 weeks
- **Mean forecast demand:** 770 visits/week (peak 835)
- **High-load weeks (utilisation ≥ 85%):** 3
- **Mean staffing:** 11.7 physicians, 7.8 nurses, 5.0 admin per week
- **Total 12-week optimised staffing cost:** $799,300

## 2. Methodology

Weekly outpatient demand is forecast with an ensemble of Facebook Prophet and a seasonal SARIMA model (weighted average), with automatic fallback to SARIMA-only or a seasonal-naive model. Forecast demand is converted into role-level staffing requirements using productivity ratios, a surge buffer is applied, and a linear program (PuLP/CBC) minimises weekly staffing cost subject to meeting the buffered demand for each role.

## 3. Weekly resource plan

| week | forecast_visits | physicians | nurses | admin_staff | projected_utilization | high_load_flag |
| --- | --- | --- | --- | --- | --- | --- |
| 2026-09-21 | 720.0 | 11 | 7 | 5 | 81.8% | False |
| 2026-09-28 | 711.3 | 11 | 7 | 5 | 80.8% | False |
| 2026-10-05 | 706.8 | 11 | 7 | 5 | 80.3% | False |
| 2026-10-12 | 709.3 | 11 | 7 | 5 | 80.6% | False |
| 2026-10-19 | 729.8 | 11 | 7 | 5 | 82.9% | False |
| 2026-10-26 | 753.7 | 11 | 8 | 5 | 85.6% | True |
| 2026-11-02 | 783.3 | 12 | 8 | 5 | 81.6% | False |
| 2026-11-09 | 799.2 | 12 | 8 | 5 | 83.3% | False |
| 2026-11-16 | 823.3 | 12 | 8 | 5 | 85.8% | True |
| 2026-11-23 | 835.0 | 13 | 9 | 5 | 80.3% | False |
| 2026-11-30 | 834.8 | 13 | 9 | 5 | 80.3% | False |
| 2026-12-07 | 828.2 | 12 | 8 | 5 | 86.3% | True |

## 4. Cost optimisation

| week | forecast_visits | physicians | nurses | admin_staff | weekly_cost |
| --- | --- | --- | --- | --- | --- |
| 2026-09-21 | 720.0 | 11 | 7 | 5 | 62500.0 |
| 2026-09-28 | 711.3 | 11 | 7 | 5 | 62500.0 |
| 2026-10-05 | 706.8 | 11 | 7 | 5 | 62500.0 |
| 2026-10-12 | 709.3 | 11 | 7 | 5 | 62500.0 |
| 2026-10-19 | 729.8 | 11 | 7 | 5 | 62500.0 |
| 2026-10-26 | 753.7 | 11 | 8 | 5 | 64600.0 |
| 2026-11-02 | 783.3 | 12 | 8 | 5 | 68400.0 |
| 2026-11-09 | 799.2 | 12 | 8 | 5 | 68400.0 |
| 2026-11-16 | 823.3 | 12 | 8 | 5 | 68400.0 |
| 2026-11-23 | 835.0 | 13 | 9 | 5 | 74300.0 |
| 2026-11-30 | 834.8 | 13 | 9 | 5 | 74300.0 |
| 2026-12-07 | 828.2 | 12 | 8 | 5 | 68400.0 |

## 5. Artefacts

- `outputs/reports/load_forecast.csv` — weekly forecast (total + per clinic)
- `outputs/reports/resource_schedule.csv` — staffing plan & utilisation
- `outputs/reports/schedule_optimization.csv` — cost-optimal staffing
- `outputs/reports/forecast_dashboard.html` — interactive dashboard
- `outputs/figures/forecast_*.png` — forecast & staffing figures
