# Implementation Roadmap: 26-Week Phase I Plan

**Project:** Integrated Demand Forecasting & Supply Optimization for Multi-Outlet Campus Dining, Saint Louis University
**Author:** Olawale Ajibola Ashaolu, Data Analyst, Sodexo

The plan has seven stages over about 26 weeks. Each stage maps to a verb of the NIW endeavor: develop, validate, deploy, or publicly disseminate. This repository records Stage 1 (access and approval), Stage 2 (inventory data ingestion), and Stage 3 (baseline analysis) for the inventory domain.

| Stage | Weeks | NIW verb | Key actions | Deliverables | Status |
|---|---|---|---|---|---|
| 1. Data discovery, access & infrastructure | 1–3 | Develop | Map all systems (POS, GrubHub, procurement, inventory, menu, enrollment); get management approval; request 24 months of history; set up the Python/SQL/GitHub environment; write the data dictionary | Data inventory; approval on file; data dictionary v1.0; GitHub repo | **Done:** approval from the Director of Operations – Retail; repo and data dictionary published |
| 2. Ingestion, cleaning & integration | 4–6 | Develop | Automated ingestion scripts; quality checks; standardize codes and dates; unified model (fact tables: transactions, orders, inventory; dimension tables: items, locations, time, plan tiers); merge GrubHub with POS; add calendar and weather | ETL pipeline; integrated database; data-quality report | **Inventory domain done:** 505-page INRHTSUM parsed, 0 reconciliation mismatches. POS, GrubHub, and enrollment next |
| 3. Exploratory analysis & baselines | 7–8 | Validate | Demand seasonality; procurement efficiency; waste and stock-out baselines; plan participation; baseline dashboards | Baseline report; seasonality profile; dashboard v1.0 | **Inventory baseline done:** 12 charts, 29 indices, 8 recommendations |
| 4. Predictive model development | 9–13 | Develop | SARIMA, Prophet, and XGBoost demand models by outlet and meal period; supply-need model (demand → order quantities); dynamic PARs and reorder triggers; meal-plan utilization model | Trained models; model documentation; feature importance | Next |
| 5. Validation & benchmarking | 14–16 | Validate | 70/30 train/test split; MAPE, RMSE, bias; compare with manual ordering; sensitivity by outlet, meal period, and season; back-cast waste, stock-out, and cost impact | Validation report; before/after tables | Planned |
| 6. Deployment | 17–20 | Deploy | Manager dashboard (next-week demand, production and order quantities, inventory alerts); automated Sunday forecast run; GrubHub live feed; manager training; 4–6 weeks of live accuracy tracking | Live dashboard; scheduled pipeline; manager sign-off; accuracy log | Planned (management has committed to implementation) |
| 7. Public dissemination | 21–26 | Disseminate | De-identified aggregate results; technical paper (e.g. *Journal of Foodservice Business Research*, INFORMS Annual Meeting, Academy of Nutrition and Dietetics conference); open-source repo; Sodexo district presentation; LinkedIn article; replicability write-up | Paper; repo; presentation record; management letter; article | **Started:** this public repository |

## Data still to be collected

| Data | Source | Priority | Purpose |
|---|---|---|---|
| Meal-swipe transactions (time, outlet, meal period, tender, plan tier) | Sodexo SLU POS | High | Core utilization and demand variable |
| GrubHub orders (time window, item, outlet, cancellations, revenue) | GrubHub Campus backend | High | High-frequency behavioral demand signal |
| Meal-plan enrollment by tier and semester | SLU / Sodexo meal-plan system | High | Population denominator |
| Sales by outlet, day, meal period, and item | POS, GrubHub, financial reports | High | Demand proxy; inventory-to-sales ratio |
| Purchase orders and vendor performance | Sodexo procurement records | High | Supply side; vendor fill rate |
| Inventory counts (weekly, already in hand), PARs (to be set) | INRHTSUM; Rec. 1 | High | Inventory baseline and decision rules |
| **Receiving log** (does not exist yet) | Set up under Rec. 5 | High | Procurement-to-inventory reconciliation |
| **Waste log** (does not exist yet) | Set up under Rec. 4 | High | Waste baseline and impact |
| Stock-out incidents (not tracked yet) | New log | High | Service-reliability baseline |
| Menu, recipe, nutrition, and allergen data | Sodexo menu platform | Medium | Clinical/nutrition analog |
| Academic calendar, events, weather (NOAA), CDC illness, USDA food prices | SLU and public APIs | Medium | External forecasting features |

## Impact metrics: record the baseline, then track

| Metric | How it is measured | Why it matters for the NIW |
|---|---|---|
| Forecast accuracy (MAPE) | Weekly MAPE by outlet and meal period vs. actual swipes and orders | Technical rigor and improvement over the manual baseline |
| Food waste reduction (lbs, $) | Waste logs before vs. after deployment | Economic and environmental impact |
| Stock-out reduction | Incidents by item and outlet before vs. after | Service reliability |
| Procurement cost savings ($) | Model-recommended vs. actual ordering cost | Hard financial impact, transferable to health-system supply chains |
| GrubHub order forecast accuracy | Predicted vs. actual daily orders by outlet and time window | Behavioral-data integration |
| Meal-plan utilization accuracy | Predicted vs. actual swipes per enrolled student by tier | Population-level modeling (patient-panel analog) |
| Manager adoption | Weekly dashboard users and actions taken | Proof of real institutional use |
| Replicability scope | Number of Sodexo U.S. campus and healthcare sites where it can be deployed | National scale of benefit |

## Governance checklist
- [x] Management approval to carry out the project and publish it (Director of Operations – Retail)
- [x] Version-controlled repository for code, data, charts, and reports
- [x] Data dictionary ([DATA_DICTIONARY.md](DATA_DICTIONARY.md))
- [ ] Signed management letter in the NIW petition file
- [ ] Aggregate or de-identified student data only in all later deliverables (swipes, enrollment, GrubHub); no individual records
- [ ] Confirm with SLU whether aggregated swipe and enrollment data needs a FERPA review
- [ ] Confirm which GrubHub backend data may be cited externally
- [ ] Project log of meetings, decisions, and access granted
