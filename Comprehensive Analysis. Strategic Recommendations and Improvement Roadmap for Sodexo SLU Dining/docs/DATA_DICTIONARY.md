# Data Dictionary and Methodology

**Author:** Olawale Ajibola Ashaolu, Data Analyst, Sodexo

## Source systems

| Source | ID / format | Coverage | Used for |
|---|---|---|---|
| Sodexo Inventory Summary History Report | INRHTSUM, PDF, 505 pages, run 10/13/2026 | 16 units × 33 report dates (28 Friday weeks + 5 month-end closes), W/E 04/03/2026 to 10/09/2026 | Inventory panel and every inventory index |
| Grand Hall Day Parts Report | POS report | 10/07/2026 (academic weekday) | Demand profile by meal period |
| Grand Hall order templates | Produce and Sysco, 409 lines each | Mon/Wed/Fri cadence | PAR and order-quantity gap |
| Revenue-center catalog | 15,145 records, 3,663 unique codes | Current | Catalog-health indices |
| GrubHub Campus backend | Dashboard and exports | Ongoing | Behavioral demand layer (Stage 2 onward) |
| Receiving logs / waste logs | **Do not exist** | — | To be set up (Rec. 4 and 5) |

The raw 505-page PDF is not kept in the repository. The parsed panel is in `data/`.

## Unit roster (codes as printed in INRHTSUM)

| Unit # | Unit name | Status | Weeks with inventory | Latest (10/09/2026) |
|---|---|---|---|---|
| 13341001 | Grand Dining (Grand Hall residential dining) | Active | 28 | $37,311.63 |
| 13341002 | Catering | Active | 28 | $29,393.10 |
| 13341023 | Starbucks | Active | 28 | $29,045.65 |
| 13341004 | Panera | Active | 28 | $25,136.70 |
| 13341011 | Fusz Simply To Go | Active | 28 | $21,430.31 |
| 13341008 | BSC Simply To Go | Active | 28 | $19,515.86 |
| 13341027 | WOW Cafe & Wingery | Active, opened 08/21/2026 | 8 | $14,122.00 |
| 13341019 | Ameren Cafe | Active | 28 | $13,073.35 |
| 13341022 | Reinert To Go | Active | 28 | $10,819.42 |
| 13341013 | Qdoba | Active | 28 | $10,506.18 |
| 13341012 | Subway | Active | 28 | $9,112.04 |
| 13341003 | Billiken Club and Grill | Closed (last count 05/15/2026) | — | $0.00 |
| 13341005 | Marketplace | Closed (last count 05/01/2026) | — | $0.00 |
| 13341021 | Einsteins (legacy) | Closed (last count 07/10/2026) | — | $0.00 |
| 13341018 | Spice Market | No inventory all period | — | $0.00 |
| 13341020 | Fueling Station | No inventory all period | — | $0.00 |

*Note: the Data Analysis Report lists Ameren Cafe (Einsteins) under 13341021. In the INRHTSUM pages, Ameren Cafe reports under **13341019** and legacy Einsteins under **13341021**. The table above follows the source report.*

## `data/inventory_weekly_panel.csv`
One row per unit per report date (505 rows, one per PDF page).

| Column | Type | Description |
|---|---|---|
| `page` | int | Source page in the INRHTSUM PDF |
| `unit_number` | str | Sodexo unit code (e.g. 13341001) |
| `unit_name` | str | Unit name as printed |
| `subsection` | str | Report subsection label |
| `week_ending` | date | Week-ending (or month-end close) date |
| `total_inventory` | float | Printed "Subsection Total" in $ (reconciled to the account lines) |
| `BAKED GOODS` … `RETAIL` | float | $ on hand by account (23 accounts): Food 6011 group (Baked Goods, Beverage, Milk & Ice Cream, Groceries, Frozen Food, Meat Eggs & Cheese, Produce), Cleaning Supplies 6022, Paper/Plastic 6026, Bar/Merchandise 6027, packaging, pharmaceuticals, retail |
| `is_friday_week` | bool | Regular Friday week-ending count |
| `is_month_end_close` | bool | Non-Friday month-end accounting close |
| `stale_carry_forward` | bool | Value identical to the unit's prior Friday week (count probably not re-taken) |

## `data/unit_performance_summary.csv`
One row per active unit: `weeks`, `start`, `end`, `min`, `max`, `mean`, `std`, and `pct_growth` (a fraction, so 0.5025 = +50.25%). All are computed on Friday weeks with inventory above $0.

## `data/key_indices.csv`
Output of `scripts/compute_indices.py`. Columns: `index` (1–29), `name`, `category`, `value`, `note`.

## `data/inventory_analysis_summary.txt`
Console summary of the full analysis run: portfolio figures, unit statistics, stale rates, CV ranking, category mix, Day Parts.

## Methodology
1. **Extraction.** `inventory_extractor.py` reads all 505 pages with PyMuPDF. It captures the unit, week-ending date, subsection, every 8-digit account line, and the printed subsection total.
2. **Reconciliation.** Account lines are summed per page and compared with the printed total. Result: **0 mismatches above $0.01** across all 505 unit-week records. The parser raised one informational flag.
3. **Weekly basis.** The analysis uses the 28 Friday week-ending counts. The 5 month-end closes are kept in the panel and flagged.
4. **Unit status.** Active means inventory above $0 on 10/09/2026. Partial means active but missing early weeks (WOW Cafe). Closed means it had inventory earlier and is now $0. Never-active means $0 in every week.
5. **Statistics.** Growth runs from first to last non-zero week. Trend slope is OLS on week index. σ uses n − 1. CV = σ ÷ mean.
6. **Stale count.** A week is stale when the unit total equals the prior Friday week exactly, to the cent. Rate = stale weeks ÷ transitions.
7. **Outlier handling.** Panera's $77,772.81 on W/E 04/10/2026 (~4.7× its average for other weeks) is kept and flagged everywhere. It is not removed.
8. **Day Parts.** Taken directly from the Grand Hall report for 10/07/2026. Average check = gross ÷ items. Low averages reflect meal-plan swipes recorded at about $0.
