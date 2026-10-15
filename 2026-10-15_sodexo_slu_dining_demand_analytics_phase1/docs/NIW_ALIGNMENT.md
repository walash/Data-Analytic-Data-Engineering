# Alignment with My EB-2 NIW Proposed Endeavor

**Olawale Ajibola Ashaolu, Data Analyst, Sodexo, Saint Louis University Dining Services**

## The proposed endeavor
> My proposed endeavor is to develop, validate, deploy, and publicly disseminate an integrated predictive analytics system that enables U.S. health institutions to combine clinical, procurement, inventory, utilization, and population-health data to forecast chronic-disease burden, clinical demand, and medical-supply needs.

This project is **Phase I** of that endeavor, carried out in a real institutional setting: Sodexo's multi-outlet dining operation at Saint Louis University. **No hospital or patient data is used.** The link to health institutions is methodological. The pipeline design, forecasting architecture, validation framework, and deployment pattern built here are the ones the endeavor applies to U.S. health institutions. Only the source systems change.

## My role and data access
As Data Analyst for Sodexo SLU Dining I analyze operational, financial, and utilization data across about six campus stores and restaurants:

- meal-swipe transactions across the dining outlets (utilization)
- supply and inventory metrics, including the weekly Inventory Summary History Report (inventory, procurement)
- meal-plan enrollment by student tier (population)
- daily and weekly sales at every location (utilization, financial)
- the **GrubHub Campus backend**: real-time online orders, order- and item-level detail, and daily revenue by outlet. This high-frequency behavioral demand layer closely mirrors patient-level utilization streams.

## The five data domains: bridge table

| Endeavor data domain | Sodexo SLU Dining equivalent | Why it is the same problem |
|---|---|---|
| **Utilization** | Meal swipes, GrubHub orders, POS sales, Day Parts demand across outlets | A defined population consuming a service at forecastable rates, the same as patient visits and bed occupancy |
| **Procurement** | Purchase orders, Produce and Sysco templates (409 lines), vendor deliveries from 6 partners | Same lead-time, fill-rate, and cost-forecasting structure as medical-supply procurement |
| **Inventory** | Weekly INRHTSUM counts for 16 units, PAR levels, receiving and waste logs (to be set up) | Perishable food inventory works like pharmaceutical, blood-product, and sterile-supply management |
| **Population-health / context** | Enrollment census, meal-plan participation, academic calendar | A campus is a bounded population, like a hospital catchment area |
| **Clinical / outcome analog** | Menu nutrition profiles, allergen and dietary accommodation data | Diet is a primary modifiable driver of chronic disease, upstream of chronic-disease burden forecasting |

## What Phase I has already delivered

| Endeavor step | Delivered in this repository | Evidence |
|---|---|---|
| **Develop** | PDF extraction pipeline (505 pages → 16-unit × 33-date panel, reconciled to the cent), visualization suite, index calculator | `scripts/`, `data/` |
| **Validate** | 29 baseline indices; data-quality audit (stale counts, outlier, catalog health, PAR coverage) | `docs/ANALYTICAL_INDICES.md`, `data/key_indices.csv` |
| **Deploy** | Eight recommendations reviewed and approved by the Director of Operations – Retail, who committed to implementing them | `docs/RECOMMENDATIONS.md`, `docs/MANAGEMENT_ENDORSEMENT.md` |
| **Publicly disseminate** | Open, documented repository with code, data, charts, and reports, published with management approval | This repository |

## Where it goes next
- **Phase I, remaining (campus dining).** Forecasting models for demand, supply need, and inventory. Validated by MAPE and RMSE on held-out data. Deployed as a weekly manager dashboard. See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md).
- **Phase II (healthcare food and nutrition services).** Sodexo already operates hospitals, long-term care, and senior living through its **Healthcare & Seniors** segment. Taking the system there is a path inside the same company.
- **Phase III (U.S. health institutions).** The same pipeline, now fed by patient census and clinic visits, medical-supply and pharmacy POs, pharmacy and sterile-supply stock, catchment demographics, and clinical outcomes. The goal is to forecast clinical demand, medical-supply needs, and chronic-disease burden.

The ETL design, feature patterns, model families (SARIMA, Prophet, gradient boosting), validation protocol (MAPE, RMSE, bias, held-out testing), and deployment pattern (scheduled pipelines, manager dashboards) all carry over unchanged.

## Why this is in the national interest
- **The gap is real and common.** At SLU, 0 of 409 procurement lines had a PAR level, and the main dining hall built up $12,478.76 of extra inventory in 28 weeks. Health-system supply chains face the same gap in forecasting and replenishment, and there the cost of over-stock, waste, and stock-outs is higher.
- **The methods are proven on real institutional data**, not simulations. They were reviewed and adopted by operations management.
- **The scale is large.** Sodexo runs hundreds of U.S. campus and healthcare accounts where this architecture could be deployed.
- **The work is public.** Code, documentation, and results are openly available for other institutions to reuse.
