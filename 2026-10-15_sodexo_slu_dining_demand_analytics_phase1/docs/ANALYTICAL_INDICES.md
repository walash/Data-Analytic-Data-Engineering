# Analytical Indices: Phase I Baseline (29 indices)

**Author:** Olawale Ajibola Ashaolu, Data Analyst, Sodexo
**Sources:** Sodexo Inventory Summary History Report (INRHTSUM, 505 pages, W/E 04/03/2026 to 10/09/2026); Grand Hall Day Parts Report (10/07/2026); Grand Hall Produce and Sysco templates; revenue-center catalog file.
**Reproduce:** `python scripts/compute_indices.py` writes `data/key_indices.csv`.

These indices are the numbers behind the eight recommendations. They are recorded **before** any model is deployed, so they serve as the "before" side of the before-and-after comparison. The full write-up is in `Sodexo_SLU_Key_Indices_Reference.docx`.

## 1. Inventory performance

| # | Index | Formula | SLU value | Use |
|---|---|---|---|---|
| 1 | Total portfolio inventory (latest week) | Σ unit inventory, active units | **$219,466.24** (11 units, W/E 10/09/2026) | Scale benchmark; NIW impact baseline |
| 2 | Average inventory per active unit | Total ÷ active units | **$19,951.48** | Cross-unit comparison |
| 3 | Growth rate | (End − Start) ÷ Start | Grand Dining **+50.25%**; Panera +62.0%; Qdoba +57.3%; Reinert To Go +48.0%; BSC +35.7%; Ameren +31.0%; Catering −1.16%; Starbucks −5.24% | Flags over-ordering (Rec. 1) |
| 4 | Weekly trend slope | OLS β of inventory on week | Grand Dining **+$307.67/week** (~$1,330/month) | Structural drift vs. seasonal swing |
| 5 | Range | Max − Min | Grand Dining $25,599.38; Starbucks $23,193.67; Panera $65,571.01 (incl. outlier) | Ordering consistency |
| 6 | Standard deviation | √[Σ(x−x̄)²/(n−1)] | Panera $12,068.12; Grand Dining $8,477.02; Fusz $6,809.88; Starbucks $5,784.07; Ameren $5,055.05; Catering $2,160.16; Subway $1,850.59; WOW $1,292.35 | Ranks volatility |
| 7 | Coefficient of variation | σ ÷ mean | Ameren **0.72**; Panera 0.65; Reinert 0.53; Fusz 0.43; Qdoba 0.34; Starbucks 0.27; Grand Dining 0.26; Subway 0.22; BSC 0.17; WOW 0.09; Catering **0.07** | CV > 0.40 marks a unit for review |
| 8 | Portfolio weekly mean | Mean of weekly totals (28 Friday weeks, 11 active units) | **$171,901.68** | Trend baseline |
| 9 | Portfolio peak / low | Max / Min of weekly totals | Peak **$263,943.40** (04/10, Panera outlier); low **$126,289.65** (07/10, summer) | Seasonal envelope |
| 10 | Unit inventory share | Unit ÷ total | Grand Dining 17.0%; Catering 13.4%; Starbucks 13.2%; Panera 11.5%; Fusz 9.8%; BSC 8.9%; WOW 6.4%; Ameren 6.0%; Reinert 4.9%; Qdoba 4.8%; Subway 4.2% (top 3 = **43.6%**) | Concentration |

## 2. Data quality

| # | Index | Formula | SLU value | Use |
|---|---|---|---|---|
| 11 | Stale count rate | Weeks equal to prior week ÷ transitions | Reinert **56%** (15/27); Ameren **56%** (15/27); Subway 48% (13/27); WOW 43% (3/7); BSC 30% (8/27); others 26% (7/27) | Count reliability (Rec. 2) |
| 12 | Catalog validation rate | Validated ÷ total records | 99.5% (15,075 / 15,145; 36 non-validated, 34 new) | Pricing / revenue-leakage risk |
| 13 | Button active rate | Active ÷ total | 4.4% active / **95.6% inactive** | Catalog bloat (Rec. 7) |
| 14 | Catalog duplication factor | Records ÷ unique codes | 4.13× (15,145 / 3,663) | Redundancy |
| 15 | PAR completion rate | Lines with PAR ÷ lines | **0%** (0 / 409) | Most critical gap (Rec. 1) |
| 16 | Order-quantity logging rate | Filled slots ÷ slots | **0%** (0 / 1,227 Mon/Wed/Fri slots) | Procurement traceability |

## 3. Demand and utilization (Grand Hall, 10/07/2026)

| # | Index | Formula | SLU value | Use |
|---|---|---|---|---|
| 17 | Items sold per meal period | POS count | Breakfast 604; Lunch 941; Dinner 992; total **2,537** | Patient-census analog |
| 18 | Average check | Gross ÷ items | Breakfast $0.32; Lunch $1.54; Dinner $0.30 | Separates meal-plan swipes from cash or retail |
| 19 | Meal-period revenue share | Period ÷ daily gross | Breakfast 10.0%; Lunch **74.7%** (74.8% of food revenue excl. tax); Dinner 15.3% | Revenue forecasting |
| 20 | Demand volume distribution | Period ÷ daily items | Breakfast 23.8%; Lunch 37.1%; Dinner **39.1%** | Production and staffing |
| 21 | Control count discrepancy | Items − control count | Breakfast 0; Lunch +4; Dinner 0; total +4 | POS integrity |

## 4. Forecasting and model performance (Phase I targets)

| # | Index | Formula | Target / status |
|---|---|---|---|
| 22 | MAPE | (1/n) Σ abs(A − F) ÷ A × 100 | < 10% strong; < 15% acceptable |
| 23 | RMSE | √[(1/n) Σ (A − F)²] | Computed in Stage 5 |
| 24 | Forecast bias | (1/n) Σ (F − A) | 0 (positive = over-ordering; negative = stock-out risk) |
| 25 | Inventory-to-sales ratio | On hand ÷ avg weekly revenue | Pending POS and GrubHub integration; targets: perishables 0.5–1.0 weeks, dry 1–2 weeks |
| 26 | Stock-out rate | Stock-out events ÷ service periods | Not tracked; baseline to be set up |
| 27 | Procurement cost variance | Actual − model-recommended cost | Target 10–20% less over-ordering cost |

## 5. Supply chain

| # | Index | SLU value | Use |
|---|---|---|---|
| 28 | Vendor concentration | 6 vendors: Ole Tyme Produce, Ecolab, Baker's Pride, Sysco, Fortune Fish, Prairie Farms. Sysco supplies most food categories | Resilience risk |
| 29 | Procurement template coverage | ≈ 11% (409 of 3,663 catalog codes) | Gap between tracked and ordered items |

**Excluded on purpose.** SLU Dining has no receiving logs and no waste logs, so fill rate and waste rate are left out until those records exist (Recommendations 4 and 5).
