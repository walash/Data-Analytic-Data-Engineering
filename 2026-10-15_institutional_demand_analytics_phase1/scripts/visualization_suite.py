"""Visualization suite — Phase I exploratory analysis.

I built this script to turn the weekly inventory panel produced by
`inventory_extractor.py` into 12 decision-support charts and a unit-level
performance summary. It also enriches the panel with three data-quality flags:

  - is_friday_week       : regular Friday week-ending count (vs. month-end close)
  - is_month_end_close   : non-Friday month-end accounting close
  - stale_carry_forward  : value identical to the prior week (count likely not re-taken)

Usage (from the project folder):
    python scripts/visualization_suite.py

Unit A is the residential (all-you-care-to-eat) dining hall and is highlighted
throughout; Unit B is catering; remaining units are retail outlets.
"""
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CHARTS = ROOT / "charts"
CHARTS.mkdir(exist_ok=True)
DPI = 200
HERO = "Unit A"          # residential dining hall
OUTLIER_UNIT = "Unit D"  # unit with the 04/10 count/keying outlier

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.titleweight": "bold", "axes.titlesize": 13})

# ---------------------------------------------------------------- data prep
df = pd.read_csv(DATA / "inventory_weekly_panel.csv", parse_dates=["week_ending"])
df["is_friday_week"] = df.week_ending.dt.dayofweek == 4
df["is_month_end_close"] = df.week_ending.dt.is_month_end
df["unit"] = df.unit_name
w = df[df.is_friday_week]
P = w.pivot(index="week_ending", columns="unit", values="total_inventory")
latest = P.index.max()
L = P.loc[latest]
active = L[L > 0].sort_values(ascending=False).index.tolist()
inactive = L[L.fillna(0) == 0].index.tolist()
never = [u for u in inactive if P[u].fillna(0).sum() == 0]
closed = [u for u in inactive if u not in never]
partial = [u for u in active if P[u].isna().any()]

# Flag weeks where the reported value equals the prior week's (carried forward)
df["stale_carry_forward"] = False
for u, g in df[df.is_friday_week].groupby("unit"):
    s = g.sort_values("week_ending")
    df.loc[s.index, "stale_carry_forward"] = (s.total_inventory.diff() == 0) & (s.total_inventory > 0)
df.drop(columns="unit").to_csv(DATA / "inventory_weekly_panel.csv", index=False)

pal = ["#0B3D91", "#2E86C1", "#5DADE2", "#148F77", "#48C9B0", "#7D3C98",
       "#AF7AC5", "#CA6F1E", "#F0B27A", "#566573", "#ABB2B9"]
col = {u: pal[i % len(pal)] for i, u in enumerate(active)}
col[HERO] = "#C0392B"


def fmt(ax):
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"${x / 1000:,.0f}K"))


def dax(ax):
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))


SRC = "Source: Inventory Summary History Report, run 10/13/2026; Friday week-ending counts."


def save(fig, name, src=SRC):
    fig.text(0.01, 0.005, src, fontsize=7.5, color="#566573")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(CHARTS / name, dpi=DPI)
    plt.close(fig)


# ---------------------------------------------------------------- 1. weekly trends
fig, ax = plt.subplots(figsize=(13, 6.5))
for u in active:
    hero = u == HERO
    ax.plot(P.index, P[u], color=col[u], lw=3.2 if hero else 1.4, alpha=1 if hero else .85,
            label=u, zorder=5 if hero else 2, marker="o" if hero else None, ms=3)
ax.axvspan(pd.Timestamp("2026-05-12"), pd.Timestamp("2026-08-10"), color="#EAECEE", alpha=.6, zorder=0)
ax.text(pd.Timestamp("2026-06-25"), ax.get_ylim()[1] * .96, "Summer session (reduced ops)",
        ha="center", color="#566573", fontsize=9)
ax.annotate(f"{OUTLIER_UNIT} 04/10: $77.8K\n(probable count/keying outlier)",
            xy=(pd.Timestamp("2026-04-10"), P.loc["2026-04-10", OUTLIER_UNIT]),
            xytext=(pd.Timestamp("2026-05-05"), 72000), fontsize=8.5,
            arrowprops=dict(arrowstyle="->", color="#566573"))
fmt(ax); dax(ax)
ax.set_ylabel("Inventory on hand ($)"); ax.set_xlabel("Week ending"); ax.grid(axis="y", alpha=.3)
ax.set_title("Weekly Inventory Trends by Dining Unit (Apr–Oct 2026)")
ax.legend(fontsize=8.5, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left")
save(fig, "01_weekly_inventory_trends_all_units.png")

# ---------------------------------------------------------------- 2. hero unit growth
g = P[HERO]
x = np.arange(len(g))
b = np.polyfit(x, g.values, 1)
fig, ax = plt.subplots(figsize=(12, 6))
ax.fill_between(g.index, g, color="#C0392B", alpha=.08)
ax.plot(g.index, g, color="#C0392B", lw=2.6, marker="o", ms=4, label=f"{HERO} weekly inventory")
ax.plot(g.index, np.polyval(b, x), "--", color="#0B3D91", lw=1.6, label=f"Linear trend ({b[0]:+,.0f} $/week)")
pct = g.iloc[-1] / g.iloc[0] - 1
ax.annotate(f"Start 04/03\n${g.iloc[0]:,.2f}", xy=(g.index[0], g.iloc[0]), xytext=(-10, -45),
            textcoords="offset points", fontsize=9, fontweight="bold", arrowprops=dict(arrowstyle="->"))
ax.annotate(f"End 10/09\n${g.iloc[-1]:,.2f}", xy=(g.index[-1], g.iloc[-1]), xytext=(-75, -55),
            textcoords="offset points", fontsize=9, fontweight="bold", arrowprops=dict(arrowstyle="->"))
ax.annotate(f"Peak ${g.max():,.0f}\n({g.idxmax():%m/%d})", xy=(g.idxmax(), g.max()), xytext=(10, 8),
            textcoords="offset points", fontsize=8.5)
ax.annotate(f"Low ${g.min():,.0f}\n({g.idxmin():%m/%d})", xy=(g.idxmin(), g.min()), xytext=(0, -35),
            textcoords="offset points", fontsize=8.5, ha="center")
ax.text(.02, .95, f"Change Apr 3 → Oct 9: {pct:+.1%}", transform=ax.transAxes, fontsize=12,
        fontweight="bold", color="#C0392B", bbox=dict(fc="white", ec="#C0392B"))
fmt(ax); dax(ax); ax.set_ylim(0, g.max() * 1.18)
ax.set_ylabel("Inventory on hand ($)"); ax.set_xlabel("Week ending"); ax.grid(axis="y", alpha=.3)
ax.legend(loc="lower right", frameon=False)
ax.set_title(f"{HERO} (Residential Dining) Inventory Growth: Apr–Oct 2026")
save(fig, "02_unit_a_growth.png")

# ---------------------------------------------------------------- 3. current inventory
s = L.reindex(active).sort_values()
allu = P.columns
fig, ax = plt.subplots(figsize=(11, 6.5))
bars = ax.barh(s.index, s.values, color=["#C0392B" if u == HERO else "#2E86C1" for u in s.index])
for r, v in zip(bars, s.values):
    ax.text(v + 300, r.get_y() + r.get_height() / 2, f"${v:,.2f}", va="center", fontsize=9)
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"${x / 1000:,.0f}K"))
ax.set_xlim(0, s.max() * 1.2); ax.set_xlabel("Inventory on hand ($)")
ax.set_title("Current On-Hand Inventory by Unit — Week Ending 10/09/2026")
ax.text(.98, .04, f"Total active portfolio: ${s.sum():,.2f} ({len(s)} units)\n$0.00 (inactive): " + ", ".join(inactive),
        transform=ax.transAxes, ha="right", fontsize=8.5, color="#7B241C", bbox=dict(fc="#FDEDEC", ec="#E6B0AA"))
save(fig, "03_current_inventory_by_unit.png")

# ---------------------------------------------------------------- 4. inventory share
s2 = L.reindex(active).sort_values(ascending=False)
fig, ax = plt.subplots(figsize=(10, 8))
ax.pie(s2, labels=[f"{u}\n{v / s2.sum():.1%}" for u, v in s2.items()], colors=[col[u] for u in s2.index],
       startangle=90, counterclock=False, wedgeprops=dict(width=.42, edgecolor="white"),
       labeldistance=1.1, textprops=dict(fontsize=9))
ax.text(0, 0, f"Total\n${s2.sum():,.0f}\n{len(s2)} active units", ha="center", va="center",
        fontsize=12, fontweight="bold")
ax.set_title("Inventory Share by Unit — Week Ending 10/09/2026")
save(fig, "04_inventory_share_pie.png")

# ---------------------------------------------------------------- 5. day-part demand (one day snapshot)
dp = pd.DataFrame({"period": ["Breakfast", "Lunch", "Dinner"], "items": [604, 941, 992],
                   "rev": [194.18, 1448.91, 296.98]})
dp["chk"] = dp.rev / dp["items"]
fig, ax = plt.subplots(figsize=(10, 6))
ax2 = ax.twinx(); ax2.spines["right"].set_visible(True)
bb = ax.bar(dp.period, dp["items"], color=["#5DADE2", "#2E86C1", "#0B3D91"], width=.55, label="Items sold")
for r, n, c in zip(bb, dp["items"], dp.chk):
    ax.text(r.get_x() + r.get_width() / 2, n / 2, f"{n:,} items\nAvg ${c:.2f}/item", ha="center",
            color="white", fontweight="bold", fontsize=9.5)
ax2.plot(dp.period, dp.rev, color="#C0392B", marker="o", lw=2.5, ms=9, label="Gross revenue ($)")
for p_, r_ in zip(dp.period, dp.rev):
    ax2.annotate(f"${r_:,.2f}", (p_, r_), xytext=(38, -4), textcoords="offset points", ha="center",
                 color="#C0392B", fontweight="bold")
ax.set_ylabel("Items sold"); ax2.set_ylabel("Gross revenue ($)", color="#C0392B")
ax.set_ylim(0, 1200); ax2.set_ylim(0, 1800)
h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)
ax.set_title(f"{HERO} Daily Demand Profile — October 7, 2026  (Total {dp['items'].sum():,} items, ${dp.rev.sum():,.2f})")
save(fig, "05_day_parts_demand.png",
     "Source: point-of-sale Day Parts report, 10/07/2026. Avg \\$/item = gross revenue ÷ items sold; "
     "low values likely reflect meal-plan swipes recorded at \\$0.")

# ---------------------------------------------------------------- 6. volatility
st = pd.DataFrame({u: dict(std=P[u][P[u] > 0].std(), mean=P[u][P[u] > 0].mean()) for u in active}).T
st["cv"] = st["std"] / st["mean"]
st = st.sort_values("std")
fig, ax = plt.subplots(figsize=(11, 6.5))
bars = ax.barh(st.index, st["std"], color=["#C0392B" if u == HERO else "#566573" for u in st.index])
for r, sd, cv in zip(bars, st["std"], st.cv):
    ax.text(sd + 150, r.get_y() + r.get_height() / 2, f"${sd:,.0f}  (CV {cv:.0%})", va="center", fontsize=9)
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"${x / 1000:,.0f}K"))
ax.set_xlim(0, st["std"].max() * 1.3); ax.set_xlabel("Standard deviation of weekly inventory ($)")
ax.set_title("Inventory Volatility by Unit (Std Dev of Weekly Inventory)")
ax.text(.98, .04, f"CV = coefficient of variation (std ÷ mean).\n{OUTLIER_UNIT} includes the 04/10 $77.8K outlier.",
        transform=ax.transAxes, ha="right", fontsize=8.5, color="#566573")
save(fig, "06_inventory_volatility.png", SRC + " Active units; weeks with $0 excluded.")

# ---------------------------------------------------------------- 7. total portfolio
T = P[active].sum(axis=1)
fig, ax = plt.subplots(figsize=(12, 6))
ax.fill_between(T.index, T, color="#2E86C1", alpha=.35)
ax.plot(T.index, T, color="#0B3D91", lw=2.2, marker="o", ms=3.5)
for d, lab, off in [(T.idxmax(), "Peak", (0, 12)), (T.idxmin(), "Low", (0, -30)),
                    (T.index[0], "Start", (25, 10)), (T.index[-1], "Latest", (-30, 12))]:
    ax.annotate(f"{lab} {d:%m/%d}\n${T[d]:,.0f}", xy=(d, T[d]), xytext=off, textcoords="offset points",
                ha="center", fontsize=8.5, fontweight="bold", arrowprops=dict(arrowstyle="-", color="#566573"))
fmt(ax); dax(ax); ax.set_ylim(0, T.max() * 1.15)
ax.set_ylabel("Total inventory on hand ($)"); ax.set_xlabel("Week ending"); ax.grid(axis="y", alpha=.3)
ax.set_title("Total Dining Portfolio Inventory — Weekly (Apr–Oct 2026)")
save(fig, "07_total_portfolio_inventory.png", SRC + f" Sum of the {len(active)} units active at 10/09/2026.")

# ---------------------------------------------------------------- 8. procurement template gap
fig, ax = plt.subplots(figsize=(11, 4.2))
cats = ["Order quantities logged", "PAR levels filled", "Items in templates"]
for i, c in enumerate(cats):
    if c == "Items in templates":
        ax.barh(i, 409, color="#2E86C1")
        ax.text(204, i, "409 items listed (produce + broadline templates)", ha="center", va="center",
                color="white", fontweight="bold")
    else:
        ax.barh(i, 409, color="#C0392B")
        ax.text(204, i, "0 of 409 filled (0%) — 409 blank", ha="center", va="center", color="white", fontweight="bold")
ax.set_yticks(range(3)); ax.set_yticklabels(cats); ax.set_xlim(0, 409); ax.set_xlabel("Number of template items")
ax.legend(handles=[Patch(color="#27AE60", label="Filled"), Patch(color="#C0392B", label="Unfilled")],
          loc="upper center", bbox_to_anchor=(.5, -.28), ncol=2, frameon=False)
ax.set_title(f"Procurement Template Completion Status — {HERO} (Produce + Broadline Orders)")
save(fig, "08_procurement_gap.png",
     "Source: produce and broadline distributor order templates. No receiving logs or waste logs are maintained.")

# ---------------------------------------------------------------- 9. unit activity status
groups = [("Active, full history", [u for u in active if u not in partial], "#27AE60"),
          ("Active, partial history (opened mid-period)", partial, "#F5B041"),
          ("Closed during period ($0 at 10/09)", closed, "#E67E22"),
          ("No inventory all period ($0)", never, "#C0392B")]
fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 6.5), gridspec_kw=dict(width_ratios=[1, 1.25]))
a1.pie([len(g_[1]) for g_ in groups], colors=[g_[2] for g_ in groups], wedgeprops=dict(width=.4, edgecolor="white"),
       startangle=90, counterclock=False, labels=[str(len(g_[1])) for g_ in groups], labeldistance=.8,
       textprops=dict(fontweight="bold", color="white", fontsize=12))
a1.text(0, 0, f"{len(allu)}\nunits", ha="center", va="center", fontsize=14, fontweight="bold")
a2.axis("off")
y = .97
for lab, us, c in groups:
    a2.text(0, y, f"■ {lab} ({len(us)})", color=c, fontsize=11, fontweight="bold", transform=a2.transAxes, va="top")
    y -= .07
    for u in us:
        nz = P[u][P[u] > 0]
        note = f"{nz.index.min():%m/%d}–{nz.index.max():%m/%d}" if len(nz) else "all weeks $0"
        a2.text(.05, y, f"{u}  ({note})", fontsize=9.5, transform=a2.transAxes, va="top")
        y -= .045
    y -= .02
fig.suptitle("Dining Unit Activity Status — As of October 2026", fontweight="bold", fontsize=13)
save(fig, "09_unit_activity_status.png", SRC + " Dates show first–last week with inventory > $0.")

# ---------------------------------------------------------------- 10. hero unit week-over-week change
dd = g.diff().dropna()
fig, ax = plt.subplots(figsize=(13, 6))
ax.bar(dd.index, dd.values, width=5,
       color=["#27AE60" if v > 0 else ("#C0392B" if v < 0 else "#ABB2B9") for v in dd])
for d, v in dd.items():
    if abs(v) > 4000:
        ax.text(d, v + (500 if v > 0 else -500), f"{v / 1000:+.1f}K", ha="center",
                va="bottom" if v > 0 else "top", fontsize=7.5)
    if v == 0:
        ax.plot(d, 0, marker="o", color="#7F8C8D", ms=4)
ax.axhline(0, color="black", lw=.8); fmt(ax); dax(ax)
ax.set_ylabel("Change vs prior week ($)"); ax.set_xlabel("Week ending"); ax.grid(axis="y", alpha=.3)
ax.text(.01, .95, f"{(dd == 0).sum()} of {len(dd)} weeks show $0 change (count carried forward, not re-taken)",
        transform=ax.transAxes, fontsize=9, color="#566573")
ax.set_title(f"{HERO}: Week-Over-Week Inventory Change (Apr–Oct 2026)")
save(fig, "10_unit_a_wow_change.png")

# ---------------------------------------------------------------- 11. category mix
cats_ = ["BAKED GOODS", "BEVERAGE", "MILK & ICE CREAM", "GROCERIES", "FROZEN FOOD",
         "MEAT EGGS & CHEESE", "PRODUCE", "CLEANING SUPPLIES", "PAPER/PLASTIC N/T"]
lw = w[w.week_ending == latest].set_index("unit").reindex(active)
other = lw["total_inventory"] - lw[cats_].fillna(0).sum(axis=1)
M = lw[cats_].fillna(0).assign(**{"OTHER (bar/merch/packaging)": other.clip(lower=0)})
fig, ax = plt.subplots(figsize=(13, 6.5))
left = np.zeros(len(M)); cp = plt.cm.tab20.colors
for i, c in enumerate(M.columns):
    ax.barh(M.index, M[c], left=left, color=cp[i * 2 % 20], label=c.title())
    left += M[c].values
ax.invert_yaxis()
ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, p: f"${x / 1000:,.0f}K"))
ax.set_xlabel("Inventory on hand ($)")
ax.legend(fontsize=8.5, frameon=False, bbox_to_anchor=(1.01, 1), loc="upper left")
ax.set_title("Inventory Composition by Category and Unit — Week Ending 10/09/2026")
save(fig, "11_category_mix_by_unit.png")

# ---------------------------------------------------------------- 12. count freshness
fr = df[df.is_friday_week & df.unit.isin(active)]
fr = fr[fr.total_inventory > 0].groupby("unit").agg(weeks=("total_inventory", "size"),
                                                     stale=("stale_carry_forward", "sum"))
fr["rate"] = fr.stale / (fr.weeks - 1)
fr = fr.sort_values("rate")
fig, ax = plt.subplots(figsize=(11, 6.5))
bars = ax.barh(fr.index, fr.rate, color=["#C0392B" if r > .4 else "#F5B041" if r > .25 else "#27AE60" for r in fr.rate])
for r, rt, s_, n in zip(bars, fr.rate, fr.stale, fr.weeks):
    ax.text(rt + .01, r.get_y() + r.get_height() / 2, f"{rt:.0%}  ({int(s_)} of {n - 1} weeks)", va="center", fontsize=9)
ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1)); ax.set_xlim(0, 1)
ax.set_xlabel("Share of weeks where reported inventory = prior week (carried forward)")
ax.set_title("Inventory Count Freshness — Weeks With Unchanged (Carried-Forward) Values")
save(fig, "12_count_freshness.png", SRC + " Active units, weeks with inventory > $0.")


# ---------------------------------------------------------------- unit performance summary
def unit_stats(u):
    s_ = P[u].dropna()
    s_ = s_[s_ > 0]
    return dict(unit=u, weeks=len(s_), start=s_.iloc[0], end=s_.iloc[-1], min=s_.min(), max=s_.max(),
                mean=s_.mean(), std=s_.std(), pct_growth=s_.iloc[-1] / s_.iloc[0] - 1)


S = pd.DataFrame([unit_stats(u) for u in active]).set_index("unit").sort_values("end", ascending=False)
S.to_csv(DATA / "unit_performance_summary.csv")

pd.set_option("display.width", 250)
print(f"Units: {len(allu)} total | {len(active)} active | partial {partial} | closed {closed} | never {never}")
print(f"Portfolio at {latest:%m/%d/%Y}: ${L[active].sum():,.2f}")
print(f"{HERO} trend slope ${b[0]:,.2f}/wk; flat weeks {(dd == 0).sum()}/{len(dd)}")
print(S.round(2).to_string())
print(f"Charts written to {CHARTS}")
