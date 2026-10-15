"""Inventory extractor — Phase I data engineering pipeline.

Author: Olawale Ajibola Ashaolu, Data Analyst, Sodexo

I wrote this module to convert the institution's weekly Inventory Summary History
Report (a multi-page PDF, one page per dining unit per week-ending date) into two
analysis-ready tables:

  1. A wide weekly panel (one row per unit-week, one column per account category)
  2. A long account-level table (one row per unit-week-account)

Each page's account lines are reconciled against the printed "Subsection Total"
so that parsing errors surface as mismatches instead of silently entering the data.

Usage:
    python scripts/inventory_extractor.py path/to/inventory_report.pdf [--out data/]

The source PDF is institutional data and is intentionally NOT stored in this repository.
"""
import argparse
import re
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd


def _to_float(text):
    return float(text.replace(",", ""))


def parse_report(pdf_path):
    """Parse every page of the report; return (unit_week_rows, account_rows, flags)."""
    doc = fitz.open(pdf_path)
    rows, accounts, flags = [], [], []
    for i, page in enumerate(doc):
        lines = [l.replace("\xa0", " ").strip() for l in page.get_text().split("\n")]
        week_ending = unit = subsection = None
        for l in lines:
            if l.startswith("W/E Date:") and "-" not in l:
                week_ending = l.split(":", 1)[1].strip()
            if l.startswith("Unit:"):
                unit = l[5:].strip()
            if l.startswith("Subsection:"):
                subsection = l[11:].strip()

        subtotal = None
        for j, l in enumerate(lines):
            # Account lines: 8-digit account code, description, amount
            if re.fullmatch(r"\d{8}", l) and j + 2 < len(lines):
                try:
                    amount = _to_float(lines[j + 2])
                except ValueError:
                    continue
                accounts.append(dict(page=i + 1, week_ending=week_ending, unit=unit,
                                     account=l, desc=lines[j + 1], amount=amount))
            if l == "Subsection Total:":
                try:
                    subtotal = _to_float(lines[j + 1])
                except ValueError:
                    flags.append((i + 1, "unreadable subtotal", lines[j + 1]))

        if unit is None or subtotal is None:
            flags.append((i + 1, "skipped page", unit, subtotal))
            continue
        unit_number, unit_name = unit.split(" - ", 1)
        rows.append(dict(page=i + 1, unit_number=unit_number, unit_name=unit_name,
                         subsection=subsection, week_ending=week_ending,
                         total_inventory=subtotal))
    return rows, accounts, flags


def build_tables(rows, accounts):
    """Build the wide weekly panel and long account table, with reconciliation."""
    panel = pd.DataFrame(rows)
    acct = pd.DataFrame(accounts)
    panel["week_ending"] = pd.to_datetime(panel.week_ending)
    acct["week_ending"] = pd.to_datetime(acct.week_ending)
    acct["unit_number"] = acct.unit.str.split(" - ").str[0]

    wide = acct.pivot_table(index="page", columns="desc", values="amount",
                            aggfunc="sum").reset_index()
    merged = panel.merge(wide, on="page", how="left")

    # Reconciliation: account lines must sum to the printed subsection total
    acct_sum = acct.groupby("page").amount.sum()
    diff = (merged.page.map(acct_sum) - merged.total_inventory).round(2)
    print(f"Reconciliation mismatches (> $0.01): {(diff.abs() > 0.01).sum()}")
    return merged, acct


def anonymize(panel, acct):
    """Replace unit codes/names with neutral labels (U01/Unit A, ...) for publication."""
    codes = sorted(panel.unit_number.unique())
    code_map = {c: f"U{i + 1:02d}" for i, c in enumerate(codes)}
    name_map = {c: f"Unit {chr(65 + i)}" for i, c in enumerate(codes)}
    panel["unit_name"] = panel.unit_number.map(name_map)
    panel["subsection"] = "000 " + panel.unit_name
    panel["unit_number"] = panel.unit_number.map(code_map)
    acct["unit"] = acct.unit_number.map(name_map)
    acct["unit_number"] = acct.unit_number.map(code_map)
    return panel, acct


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf", help="Path to the Inventory Summary History Report PDF")
    ap.add_argument("--out", default="data", help="Output directory (default: data/)")
    ap.add_argument("--anonymize", action="store_true",
                    help="Replace unit names/codes with neutral labels")
    args = ap.parse_args()

    rows, accounts, flags = parse_report(args.pdf)
    print(f"Pages parsed: {len(rows)} | flags: {len(flags)}")
    panel, acct = build_tables(rows, accounts)
    if args.anonymize:
        panel, acct = anonymize(panel, acct)

    print(panel.groupby(["unit_number", "unit_name"]).agg(
        weeks=("week_ending", "count"), first=("week_ending", "min"),
        last=("week_ending", "max"), latest=("total_inventory", "last")).to_string())
    print(f"Distinct report dates: {panel.week_ending.nunique()}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    panel.to_csv(out / "inventory_weekly_panel.csv", index=False)
    acct.to_csv(out / "inventory_accounts_long.csv", index=False)


if __name__ == "__main__":
    main()
