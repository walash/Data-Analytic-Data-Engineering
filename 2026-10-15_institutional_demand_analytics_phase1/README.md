# Institutional Demand Analytics — Data Engineering & Predictive Infrastructure (Phase I)

**Author:** Olawale Ajibola Ashaolu, Data Analyst, Sodexo

*Phase I of a multi-phase data engineering initiative building the predictive analytics infrastructure described in my EB-2 NIW proposed endeavor.*

---

## NIW Proposed Endeavor

> My proposed endeavor is to develop, validate, deploy, and publicly disseminate an integrated predictive analytics system that enables U.S. health institutions to combine clinical, procurement, inventory, utilization, and population-health data to forecast chronic-disease burden, clinical demand, and medical-supply needs.

---

## 1. Project Overview

Phase I is the exploratory data analysis (EDA) and data-infrastructure foundation of the initiative. It focuses on procurement and inventory operations at a multi-outlet campus dining institution: 16 dining units, including a residential dining hall, a catering operation, and retail outlets.

In this phase I:

- engineered a reproducible pipeline that converts institutional PDF inventory reports into a clean, reconciled longitudinal dataset;
- profiled inventory levels, volatility, category mix, seasonality, and count quality across all units;
- documented a library of analytical indices that will serve as features for demand and supply forecasting models.

The methodology is domain-agnostic. Data ingestion, reconciliation, panel construction, and index design carry over directly to clinical procurement and utilization data in later phases.

## 2. Phase Structure

| Phase | Scope | Status |
|---|---|---|
| **Phase I** | Campus dining: procurement, inventory, and transaction-demand analytics | **Current** |
| **Phase II** | Clinical procurement datasets: applying the pipeline and indices to medical-supply and utilization data | Planned |
| **Phase III** | Integrated health forecasting system: combined clinical, procurement, inventory, utilization, and population-health forecasting | Planned |

## 3. Data Assets

- **28-week longitudinal panel** (April–October 2026) of Friday week-ending inventory valuations for every dining unit, broken out by 23 account categories (produce, groceries, meat/eggs/cheese, beverage, paper/plastic, cleaning supplies, and others).
- **505 unit-week inventory records** parsed from the institution's *Inventory Summary History Report* (33 report dates, including month-end closes). Every record is reconciled against the printed subsection total.
- **GrubHub transaction backend** (order timestamps, item-level sales, daily volumes). This is the supplementary demand signal being integrated alongside meal-swipe and point-of-sale data for demand forecasting.
- **Known data gaps:** the institution keeps no receiving logs and no waste logs. Phase I measures inventory and demand directly and treats these gaps as findings.

All unit names and cost-center codes in this repository have been anonymized (`Unit A` … `Unit P`, `U01` … `U16`). The source PDF reports are institutional records and are not included.

## 4. Analytical Deliverables

- **29 analytical indices** documented (see `docs/Analytical_Indices_Reference.docx`), covering inventory growth, volatility (CV), portfolio share, count freshness, procurement-template completion, and day-part demand.
- **12 visualizations** (`charts/`), covering weekly trends, residential-hall growth and week-over-week change, current inventory and portfolio share, volatility, total portfolio trend, procurement-template gaps, unit activity status, category mix, and count freshness.
- **Unit-level performance benchmarking** (`data/unit_performance_summary.csv`): start/end, min/max, mean, standard deviation, and percentage growth for each active unit.

Headline findings:

- Unit A (residential dining) inventory grew **+50.2%** between April 3 and October 9, 2026.
- Total active-portfolio inventory was **$219,466** at October 9, 2026, across 11 active units.
- **7 of 27** week-over-week values for Unit A were exact carry-forwards, which means those counts were probably not re-taken.
- **0 of 409** produce and broadline order-template items had PAR levels or order quantities filled in.

## 5. Repository Structure

```
2026-10-15_institutional_demand_analytics_phase1/
├── README.md
├── requirements.txt
├── .gitignore
├── scripts/
│   ├── inventory_extractor.py      # PDF → reconciled weekly panel (+ optional anonymization)
│   └── visualization_suite.py      # panel → 12 charts + unit performance summary
├── data/
│   ├── inventory_weekly_panel.csv  # 505 unit-week rows × 23 account categories + QA flags
│   └── unit_performance_summary.csv
├── charts/                         # 12 PNG visualizations (200 dpi)
└── docs/
    ├── Institutional_Demand_Analytics_Report.docx
    ├── Analytical_Indices_Reference.docx
    ├── NIW_Phase1_Project_Blueprint.docx
    └── NIW_Comprehensive_Analysis.docx
```

## 6. Technical Stack

- **Python**: pandas, NumPy, matplotlib, seaborn, PyMuPDF / pdfplumber (PDF parsing), python-docx, openpyxl
- **Pipeline design**: a structured extract → reconcile → panelize → enrich → visualize flow, with data-quality flags (`is_friday_week`, `is_month_end_close`, `stale_carry_forward`) stored alongside the data

## 7. How to Run

```bash
cd 2026-10-15_institutional_demand_analytics_phase1
pip install -r requirements.txt

# 1. Extract (requires access to the institutional PDF report; not distributed)
python scripts/inventory_extractor.py path/to/inventory_report.pdf --out data/ --anonymize

# 2. Build the visualizations and unit performance summary
python scripts/visualization_suite.py
```

Step 2 runs directly on the anonymized panel in `data/`, so you can reproduce the charts without the source PDF.

## 8. NIW Relevance

Phase I shows hands-on capacity to build an end-to-end data pipeline from raw, heterogeneous institutional records to decision-ready analytics. The work covers:

- parsing semi-structured PDF reports;
- reconciling every record against control totals;
- building a longitudinal panel;
- flagging data-quality failures such as carried-forward counts and blank procurement templates;
- turning the results into indices and visualizations that operations leaders can act on.

Clinical demand and medical-supply forecasting rest on the same foundations. Hospitals and health systems face the same problems: fragmented procurement and inventory records, inconsistent count discipline, and limited forecasting infrastructure. The pipeline, indices, and benchmarking methods developed here are the reusable core that Phases II and III will extend to clinical procurement, utilization, and population-health data. That extension is the integrated predictive system described in my proposed endeavor.

## 9. Author

**Olawale Ajibola Ashaolu**<br>
Data Analyst, Sodexo · Independent Data & Analytics Engineer
