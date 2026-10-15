"""Compute the Phase I key indices from the weekly inventory panel.

Author: Olawale Ajibola Ashaolu, Data Analyst, Sodexo

Recomputes the data-driven indices in docs/ANALYTICAL_INDICES.md straight from
data/inventory_weekly_panel.csv so every number in the report can be traced
back to code. Indices that come from other source documents (catalog file,
procurement templates, Day Parts report) are entered as constants below with
their source noted.

Usage (from the project folder):
    python scripts/compute_indices.py            # prints the table, writes data/key_indices.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

df = pd.read_csv(DATA / "inventory_weekly_panel.csv", parse_dates=["week_ending"])
fri = df[df.week_ending.dt.dayofweek == 4]
P = fri.pivot(index="week_ending", columns="unit_name", values="total_inventory")
latest = P.index.max()
L = P.loc[latest]
active = L[L > 0].sort_values(ascending=False).index
T = P[active].sum(axis=1)

rows = []


def add(n, name, cat, value, note=""):
    rows.append(dict(index=n, name=name, category=cat, value=value, note=note))


# ---- Inventory performance (1-10)
add(1, "Total portfolio inventory (latest week)", "Inventory", round(L[active].sum(), 2), f"{len(active)} active units, W/E {latest:%m/%d/%Y}")
add(2, "Average inventory per active unit", "Inventory", round(L[active].mean(), 2))
for u in ["Grand Dining", "Panera", "Qdoba", "Reinert To Go", "Catering", "Starbucks"]:
    s = P[u].dropna(); s = s[s > 0]
    add(3, f"Growth rate - {u}", "Inventory", round((s.iloc[-1] / s.iloc[0] - 1) * 100, 2), "% first to last week")
gd = P["Grand Dining"]
slope = np.polyfit(np.arange(len(gd)), gd.values, 1)[0]
add(4, "Trend slope - Grand Dining", "Inventory", round(slope, 2), f"$/week (~${slope * 4.33:,.0f}/month)")
add(5, "Range - Grand Dining", "Inventory", round(gd.max() - gd.min(), 2))
cv = {}
for u in active:
    s = P[u][P[u] > 0]
    add(6, f"Std dev - {u}", "Inventory", round(s.std(), 2))
    cv[u] = s.std() / s.mean()
for u, v in sorted(cv.items(), key=lambda kv: -kv[1]):
    add(7, f"Coefficient of variation - {u}", "Inventory", round(v, 2), "flag if > 0.40" if v > 0.40 else "")
add(8, "Portfolio weekly mean", "Inventory", round(T.mean(), 2), f"{len(T)} Friday weeks, {len(active)} active units")
add(9, "Portfolio peak", "Inventory", round(T.max(), 2), f"{T.idxmax():%m/%d/%Y}")
add(9, "Portfolio low", "Inventory", round(T.min(), 2), f"{T.idxmin():%m/%d/%Y}")
share = (L[active] / L[active].sum() * 100).round(1)
for u, v in share.items():
    add(10, f"Inventory share - {u}", "Inventory", v, "%")
add(10, "Top-3 unit share", "Inventory", round(share.iloc[:3].sum(), 1), "%")

# ---- Data quality (11-16)
for u in active:
    s = P[u].dropna(); s = s[s > 0]
    d = s.diff().dropna()
    add(11, f"Stale count rate - {u}", "Data quality", round((d == 0).mean() * 100), f"{int((d == 0).sum())} of {len(d)} transitions")
add(12, "Catalog validation rate", "Data quality", 99.5, "15,075 / 15,145 records (revenue-center catalog)")
add(13, "Button active rate", "Data quality", 4.4, "666 / 15,145 active; 95.6% inactive")
add(14, "Catalog duplication factor", "Data quality", 4.13, "15,145 records / 3,663 unique codes")
add(15, "PAR level completion rate", "Data quality", 0.0, "0 / 409 lines (Grand Hall Produce + Sysco templates)")
add(16, "Order quantity logging rate", "Data quality", 0.0, "0 / 1,227 Mon-Wed-Fri slots")

# ---- Demand & utilization (17-21) - Grand Hall Day Parts, 10/07/2026
dp = pd.DataFrame({"period": ["Breakfast", "Lunch", "Dinner"], "items": [604, 941, 992],
                   "food": [189.68, 1426.92, 290.83], "gross": [194.18, 1448.91, 296.98],
                   "control": [604, 937, 992]}).set_index("period")
for p_, r in dp.iterrows():
    add(17, f"Items sold - {p_}", "Demand", r["items"])
    add(18, f"Average check - {p_}", "Demand", round(r.gross / r["items"], 2), "gross / items")
    add(19, f"Revenue share - {p_}", "Demand", round(r.gross / dp.gross.sum() * 100, 1), "% of gross")
    add(20, f"Volume share - {p_}", "Demand", round(r["items"] / dp["items"].sum() * 100, 1), "% of items")
    add(21, f"Control count discrepancy - {p_}", "Demand", int(r["items"] - r.control))
add(19, "Lunch share of food revenue", "Demand", round(dp.food["Lunch"] / dp.food.sum() * 100, 1), "% of food revenue (excl. tax)")

# ---- Forecasting targets (22-27) and supply chain (28-29)
add(22, "MAPE target", "Forecasting", "<10% strong / <15% acceptable", "computed in Stage 5")
add(23, "RMSE", "Forecasting", "pending", "Stage 5")
add(24, "Forecast bias target", "Forecasting", 0)
add(25, "Inventory-to-sales ratio", "Forecasting", "pending", "needs POS / GrubHub sales integration")
add(26, "Stock-out rate", "Forecasting", "not tracked", "baseline must be established")
add(27, "Procurement cost variance target", "Forecasting", "10-20% over-order cost reduction")
add(28, "Vendor concentration", "Supply chain", 6, "Ole Tyme Produce, Ecolab, Baker's Pride, Sysco, Fortune Fish, Prairie Farms")
add(29, "Procurement template coverage", "Supply chain", round(409 / 3663 * 100, 1), "% (409 / 3,663 catalog codes)")

out = pd.DataFrame(rows)
out.to_csv(DATA / "key_indices.csv", index=False)
pd.set_option("display.width", 200, "display.max_rows", 200, "display.max_colwidth", 70)
print(out.to_string(index=False))
