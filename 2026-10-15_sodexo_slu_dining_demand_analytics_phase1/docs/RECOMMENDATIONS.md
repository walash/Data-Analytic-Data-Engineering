# Recommendations: Sodexo SLU Dining, Phase I

**Author:** Olawale Ajibola Ashaolu, Data Analyst, Sodexo, Saint Louis University Dining Services
**Evidence base:** Sodexo Inventory Summary History Report (INRHTSUM, 505 pages, W/E 04/03/2026 to 10/09/2026), Grand Hall Day Parts Report (10/07/2026), Grand Hall Produce and Sysco order templates, and the revenue-center catalog file.
**Status:** Reviewed and approved by the Director of Operations – Retail, who committed to implementing them (see [MANAGEMENT_ENDORSEMENT.md](MANAGEMENT_ENDORSEMENT.md)).

Each recommendation lists the finding, the action, who owns it, how success will be measured, and how it feeds the NIW proposed endeavor. All percentage reductions are **targets** to be checked against post-implementation data. None of them is a measured result yet.

## Summary

| # | Recommendation | Priority | Main evidence | Success measure |
|---|---|---|---|---|
| 1 | Set PAR levels for all 409 Grand Hall procurement lines | **CRITICAL** | 0 of 409 PAR levels; Grand Dining +50.25% at +$307.67/week | PAR completion 100%; Grand Dining trend slope ≤ $0/week; 15–25% less over-ordering |
| 2 | Weekly physical counts at Reinert To Go, Ameren Cafe, Subway | **HIGH** | Stale-count rates of 56%, 56%, 48% | Stale rate < 10% at every unit |
| 3 | Investigate and restate the Panera outlier | **HIGH** | $77,772.81 on W/E 04/10/2026 (~4.7× typical) | Corrected value documented; baseline restated |
| 4 | Set up waste tracking from scratch | **HIGH** | No waste log exists | Daily logs at every production outlet for one semester |
| 5 | Set up receiving verification records | **HIGH** | No receiving log exists | 100% of deliveries logged; weekly PO reconciliation |
| 6 | Review the Starbucks inventory-to-sales ratio | MEDIUM | $29,045.65 on hand (13.2% of portfolio), Beverage-heavy | 15–20% less Beverage on hand with no service impact |
| 7 | Deactivate inactive units in the catalog | MEDIUM | 5 closed or never-active units still listed | Active roster = 11 operating units |
| 8 | Build the demand forecasting model | **STRATEGIC** | 28-week panel plus Day Parts demand | MAPE < 10–15%; 10–20% less over-ordering cost |

---

## Recommendation 1: Set PAR levels for all 409 procurement lines (CRITICAL)

**Finding.** Grand Hall has two procurement templates, Produce and Sysco. Each has 409 line items and a Monday/Wednesday/Friday ordering cadence. The PAR column and all three order-quantity columns are blank on every line: 0 of 409 PAR levels and 0 of 1,227 order slots are filled. Over the same 28 weeks, Grand Dining inventory grew from $24,832.87 to $37,311.63, a gain of $12,478.76 (+50.25%) on a linear trend of +$307.67 per week, or about $1,330 per month. That pattern fits ordering with no stock target.

**Action.**
1. Work with the Executive Chef and General Manager to set a target PAR, a reorder point, and a maximum for every line in both templates. Use the 28-week inventory history as the starting baseline.
2. Base the PARs on days of cover by category:
   - perishables (produce, dairy, fresh protein): 3–5 days
   - dry goods and groceries: 7–10 days
   - frozen: 14 days
3. Record the order quantity in the Mon/Wed/Fri columns every time an order is placed, so each order leaves an audit trail.
4. Re-check the PARs monthly against the weekly counts. After Recommendation 8 is in place, replace the fixed PARs with forecast-driven ones.

**Owner.** Executive Chef and General Manager. The Data Analyst supplies the baseline tables and the monthly review.

**Success measures.** PAR completion rate of 100%. Order-quantity logging rate of 100%. Grand Dining weekly trend slope flat or negative. A target 15–25% reduction in over-ordering, measured against the 28-week baseline.

**NIW link.** This is the same decision rule a hospital uses for medical-supply replenishment. Moving from no PARs to fixed PARs to forecast-driven PARs is the *develop → deploy* path of the proposed endeavor.

## Recommendation 2: Mandate weekly physical counts at Reinert To Go, Ameren Cafe and Subway (HIGH)

**Finding.** A week counts as stale when a unit's total inventory equals the prior week's value to the cent. Stale-count rates:
- Reinert To Go: 56% (15 of 27 transitions)
- Ameren Cafe: 56% (15 of 27)
- Subway: 48% (13 of 27)
- BSC Simply To Go: 30% (8 of 27)
- WOW Cafe & Wingery: 43% (3 of 7)
- every other active unit: about 26% (7 of 27)

Values that repeat this often point to counts carried forward rather than physically re-taken. Ameren Cafe (CV 0.72) and Reinert To Go (CV 0.53) also have the highest relative volatility, which fits occasional catch-up counts.

**Action.**
1. Require a documented weekly physical count at all units.
2. Name a responsible manager at Reinert To Go and Ameren Cafe.
3. Make count submission part of the weekly reporting workflow.
4. Find out why counts have not been updated at these units (staffing, timing, or system access).

**Owner.** Unit managers, with oversight from the Director of Operations – Retail.

**Success measures.** Stale-count rate under 10% at every unit, recomputed each week with `scripts/compute_indices.py`.

**NIW link.** Any forecasting system depends on trustworthy inputs. The stale-count audit is the data-quality layer the endeavor applies to hospital supply counts.

## Recommendation 3: Investigate and resolve the Panera outlier (HIGH)

**Finding.** Panera (unit 13341004) recorded $77,772.81 for W/E 04/10/2026, about 4.7 times its average for the other weeks. That one record lifts the portfolio peak to $263,943.40. With a typical Panera week (~$16,700), the 04/10 total would be about $202,900. The record is most likely a keying error.

**Action.** Review the 04/10/2026 Panera submission. If it is confirmed wrong, restate the corrected value and record the correction. Until then the record stays in `data/` and is flagged in every analysis.

**Owner.** Panera manager and the Sodexo accounting contact.

**Success measures.** Corrected value on file. Portfolio baseline and volatility figures restated.

**NIW link.** Outlier detection and documented correction are part of the *validate* step. A forecasting model trained on that week as-is would learn a false spike.

## Recommendation 4: Set up waste tracking from scratch (HIGH)

**Finding.** SLU Dining has no waste log, no waste tracking system, and no waste data. Without them, full food cost cannot be analyzed, and the effect of any production-planning change cannot be measured.

**Action.**
1. Start a daily waste log at every production outlet. Record item, quantity, station, and reason code (overproduction, expired, damaged, customer return).
2. Use a simple tablet or phone form, or a printed daily sheet that is keyed in weekly.
3. Start right away and keep it running for at least one full semester before modeling.
4. For Grand Hall, record waste by station: Simple Servings, Leaves – Mix Green, Grills, Bliss, Plant Grown Based, Showcase Flavor Destination, Pizza Slices and Such, Savory, and Stucked Deli.

**Owner.** Executive Chef and station leads. The Data Analyst builds the form and the weekly summary.

**Success measures.** Logs completed on at least 95% of operating days. A waste $/week baseline established by the end of the semester.

**NIW link.** Waste is one of the two failure modes the system is built to reduce, the other being stock-outs. This log creates the "before" measurement.

## Recommendation 5: Set up receiving verification records (HIGH)

**Finding.** No receiving or intake documentation exists. So delivered quantities cannot be checked against orders, shrinkage between the dock and production cannot be detected, and vendor fill rates cannot be measured. The six supply partners are Ole Tyme Produce, Ecolab, Baker's Pride, Sysco, Fortune Fish, and Prairie Farms. Sysco covers most food categories.

**Action.** Start a receiving log with PO number, vendor, expected quantity, received quantity, condition note, and receiver signature. Reconcile it against purchase orders every week. Use it to report fill rate and on-time delivery by vendor.

**Owner.** Receiving staff and the General Manager.

**Success measures.** 100% of deliveries logged. Weekly reconciliation completed. A fill-rate baseline for each vendor.

**NIW link.** This closes the procurement-to-inventory chain, which is the *procurement* domain named in the endeavor. It is the same structure as hospital supply-chain receiving.

## Recommendation 6: Review the Starbucks inventory-to-sales ratio (MEDIUM)

**Finding.** Starbucks (unit 13341023) holds $29,045.65, which is 13.2% of the portfolio and nearly as much as Grand Dining. Its inventory is mostly Beverage. It is also volatile: it fell to a low of $15,598.65 mid-period, peaked at $38,792.32, and has a CV of 0.27.

**Action.** Compare Starbucks with other campus Starbucks units in the Sodexo portfolio. Set Beverage PARs from daily sales velocity (POS and GrubHub) and review them monthly.

**Owner.** Starbucks manager and the Director of Operations – Retail.

**Success measures.** A target 15–20% reduction in on-hand Beverage inventory with no drop in service levels. Inventory-to-sales ratio tracked monthly.

## Recommendation 7: Deactivate inactive units in the catalog (MEDIUM)

**Finding.** Seven units are listed but not operating:
- never recorded inventory: Spice Market (13341018), Fueling Station (13341020)
- closed during the period: Billiken Club and Grill (13341003, last count 05/15/2026), Marketplace (13341005, last count 05/01/2026), legacy Einsteins (13341021, last count 07/10/2026)

All of them still appear in reporting. Separately, the revenue-center catalog has 95.6% of its button records inactive and a 4.13× duplication factor (15,145 records for 3,663 unique codes).

**Action.** Confirm with the District Manager. Archive the closed and never-active units from the active roster. Clean up inactive catalog buttons.

**Owner.** Director of Operations – Retail and District Manager.

**Success measures.** The active roster matches the 11 operating units. Button active rate and duplication factor improve.

## Recommendation 8: Build the demand forecasting model (STRATEGIC)

**Finding.** The 28-week by 11-unit panel and the Day Parts data are enough to start time-series work. The seasonal signal is already clear: a summer low of $126,289.65 on 07/10/2026, then the fall recovery. Day Parts shows Dinner has the highest volume (992 items, 39.1%) while Lunch brings in 74.8% of food revenue.

**Action.**
1. Export 24 months of Day Parts and sales history from POS, plus GrubHub order history.
2. Merge these with the inventory panel, meal-plan enrollment, and the academic calendar.
3. Build SARIMA, Prophet, and gradient-boosting models for Grand Dining demand by meal period.
4. Turn the forecasts into recommended order quantities (forecast-driven PARs).
5. Validate on held-out data (MAPE, RMSE, bias). Deploy as a weekly manager dashboard.

**Owner.** Data Analyst, with management sponsorship.

**Success measures.** MAPE under 10% (strong) or under 15% (acceptable). A target 10–20% reduction in over-ordering cost. Weekly manager use of the dashboard.

**NIW link.** This is Stage 4 onward of the 26-week plan, the endeavor itself being built on real institutional data. See [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md).

---

### Immediate priorities agreed for implementation
1. PAR levels for all 409 lines (Rec. 1)
2. Weekly physical counts at Reinert To Go and Ameren Cafe (Rec. 2)
3. Investigation of Grand Dining accumulation by subcategory (Rec. 1 and 8)
4. Waste and receiving logs started in parallel (Rec. 4 and 5), so that the effect of 1–3 can be measured
