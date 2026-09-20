# Emergency Department Capacity Planning Report

**Project:** CVD Risk Stratification & ED Capacity Planning
**Forecast window:** 2026-09-01 to 2026-10-30 (60 days)
**Generated:** 2026-09-20 05:30

---

## 1. Executive Summary

This report converts a 60-day, CVD risk-stratified forecast of Emergency
Department (ED) demand into an actionable resource-allocation and staffing plan.
Demand is forecast per risk group (Low / Medium / High) using an **ensemble of
Facebook Prophet and SARIMA** models. Forecasts drive risk-based resource rules,
a **20% surge buffer**, capacity-utilisation monitoring, and a linear-programming
staffing optimiser.

| Metric | Value |
|---|---|
| Total forecasted ED visits | 775 |
| High-demand days (>85% utilisation) | 3 |
| Mean capacity utilisation | 69% |
| Peak capacity utilisation | 89% |
| Peak total beds required | 13 |
| Peak ICU beds required | 8 |
| Peak physicians required | 4 |
| Peak nurses required | 8 |
| Optimised 7-day staffing cost | $127,400 |

---

## 2. Methodology

### 2.1 Demand Forecasting
- **Data:** daily ED visit counts by CVD risk group from the feature store
  (`data/feature_store/ed_demand_timeseries.csv`), with risk labels sourced from
  the trained `RiskStratifier`.
- **Models:** Prophet (weekly + yearly seasonality, 95% intervals) and SARIMA
  `(1,1,1)(1,1,1)_7`. The two are combined as a weighted **ensemble**
  (60% Prophet / 40% SARIMA). SARIMA also serves as the fallback if Prophet is
  unavailable.
- **Training window:** trailing 22 months;
  **horizon:** 60 days.

### 2.2 Resource Allocation Rules
| Risk group | Intensity | Bed type | Key resources |
|---|---|---|---|
| High | 3 | ICU bed | Cardiologist consult, echo, troponin labs |
| Medium | 2 | Monitored bed | ECG, basic labs |
| Low | 1 | Standard ED bay | Basic triage |

A **20% surge buffer** is applied to every bed, staff, equipment and lab
estimate. Days with capacity utilisation above **85%** are flagged
`HIGH_DEMAND`.

### 2.3 Optimisation
A linear program (PuLP / CBC) minimises daily staffing cost subject to demand
coverage, minimum staffing floors, and surge-adjusted headcount ceilings, over a
rolling 7-day horizon.

---

## 3. Peak-Day Snapshot (2026-10-18, Sunday)

- Capacity utilisation: **89%**
- Total beds: **13**
  (ICU 8, monitored 3,
  standard 2)
- Staff: **4** physicians,
  **8** nurses, **2** techs
- Equipment: 2 ECG, 2 echo;
  labs: 20 draws

---

## 4. High-Demand Alert Days (first 15)

| Date | Day | Utilisation | Beds | Physicians | Nurses |
|---|---|---|---|---|---|
| 2026-10-18 | Sunday | 89% | 13 | 4 | 8 |
| 2026-10-24 | Saturday | 89% | 13 | 4 | 8 |
| 2026-10-25 | Sunday | 89% | 13 | 4 | 8 |

---

## 5. Optimised 7-Day Staffing Schedule

| Date | Day | Physicians | Nurses | Techs | Daily cost | Coverage OK |
|---|---|---|---|---|---|---|
| 2026-09-01 | Tuesday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-02 | Wednesday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-03 | Thursday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-04 | Friday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-05 | Saturday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-06 | Sunday | 3 | 8 | 2 | $18,200 | yes |
| 2026-09-07 | Monday | 3 | 8 | 2 | $18,200 | yes |

**Total optimised weekly staffing cost: $127,400**

---

## 6. Outputs

| Artifact | Path |
|---|---|
| Demand forecast (CSV) | `outputs/reports/ed_demand_forecast.csv` |
| Resource allocation plan (CSV) | `outputs/reports/resource_allocation_plan.csv` |
| Optimisation schedule (CSV) | `outputs/reports/capacity_optimization.csv` |
| Interactive dashboard (HTML) | `outputs/reports/capacity_dashboard.html` |
| Forecast & dashboard figures | `outputs/figures/ed_capacity_*.png`, `outputs/figures/ed_demand_forecast_*.png` |

---

## 7. Recommendations

1. **Pre-position ICU capacity and cardiology on flagged HIGH_DEMAND days** -
   these concentrate the High-risk (intensity-3) load.
2. **Adopt the optimised staffing schedule** to meet coverage at minimum cost;
   revisit weekly as new actuals arrive.
3. **Maintain the 20% surge buffer** as a floor; widen it ahead of seasonal
   peaks visible in the weekly-pattern figure.
4. **Refresh forecasts weekly** and monitor forecast-vs-actual drift to keep the
   Prophet/SARIMA ensemble calibrated.
