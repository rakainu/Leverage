"""Generate 24h scalping performance chart for SOL and ZEC."""
from datetime import datetime
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

trades = [
    ("SOL-USDT","short",85.06,84.55,17.99,"trail_sl","2026-04-10T21:38:04","2026-04-11T01:31:12",13988),
    ("SOL-USDT","long",84.71,84.23,-17.00,"drift","2026-04-11T02:47:42","2026-04-11T03:55:51",4088),
    ("ZEC-USDT","long",370.59,374.28,29.87,"trail_sl","2026-04-11T04:15:51","2026-04-11T05:45:50",5398),
    ("ZEC-USDT","short",371.97,374.02,-16.53,"drift","2026-04-11T07:55:19","2026-04-11T08:06:01",641),
    ("ZEC-USDT","short",376.05,372.76,26.25,"trail_sl","2026-04-11T11:05:51","2026-04-11T12:26:00",4809),
    ("SOL-USDT","long",84.20,84.50,10.69,"trail_sl","2026-04-11T12:52:25","2026-04-11T15:46:47",10462),
    ("ZEC-USDT","short",377.25,378.58,-10.58,"drift","2026-04-11T18:24:34","2026-04-11T18:37:05",751),
    ("SOL-USDT","short",84.76,85.58,-29.02,"drift","2026-04-11T18:17:54","2026-04-11T18:37:39",1184),
    ("ZEC-USDT","short",374.52,372.00,20.19,"trail_sl","2026-04-11T19:41:09","2026-04-11T20:26:18",2708),
    ("SOL-USDT","short",85.95,85.31,22.34,"trail_sl","2026-04-11T19:46:05","2026-04-11T20:37:23",3078),
    ("ZEC-USDT","long",371.66,370.75,-7.35,"trail_sl","2026-04-12T01:08:37","2026-04-12T01:33:21",1484),
    ("SOL-USDT","short",84.99,83.44,54.71,"trail_sl","2026-04-11T23:25:21","2026-04-12T01:36:56",7894),
]

trades.sort(key=lambda t: t[7])

def stats(rows):
    wins = [r for r in rows if r[4] > 0]
    losses = [r for r in rows if r[4] <= 0]
    pnl = sum(r[4] for r in rows)
    wr = (len(wins) / len(rows) * 100) if rows else 0
    avg_win = (sum(r[4] for r in wins) / len(wins)) if wins else 0
    avg_loss = (sum(r[4] for r in losses) / len(losses)) if losses else 0
    gross_win = sum(r[4] for r in wins)
    gross_loss = abs(sum(r[4] for r in losses))
    pf = (gross_win / gross_loss) if gross_loss else float("inf")
    avg_dur = (sum(r[8] for r in rows) / len(rows) / 60) if rows else 0
    best = max((r[4] for r in rows), default=0)
    worst = min((r[4] for r in rows), default=0)
    return dict(n=len(rows), w=len(wins), l=len(losses), wr=wr, pnl=pnl,
                avg_win=avg_win, avg_loss=avg_loss, pf=pf, avg_dur=avg_dur,
                best=best, worst=worst)

sol = [t for t in trades if t[0] == "SOL-USDT"]
zec = [t for t in trades if t[0] == "ZEC-USDT"]
s_sol, s_zec, s_all = stats(sol), stats(zec), stats(trades)

fig = plt.figure(figsize=(15, 10), facecolor="#0f1117")
fig.suptitle("Scalping Bridge — 24h Performance (SOL & ZEC)",
             color="white", fontsize=18, fontweight="bold", y=0.98)

for ax in []: pass

# ---- 1. Equity curve ----
ax1 = plt.subplot2grid((3, 3), (0, 0), colspan=2)
ax1.set_facecolor("#161a23")
cum = 0
xs, ys = [0], [0]
colors = []
for i, t in enumerate(trades, 1):
    cum += t[4]
    xs.append(i)
    ys.append(cum)
    colors.append("#2ecc71" if t[4] > 0 else "#e74c3c")
ax1.plot(xs, ys, color="#4da6ff", linewidth=2.2, marker="o", markersize=7,
         markerfacecolor="#4da6ff", markeredgecolor="white", markeredgewidth=0.8)
ax1.fill_between(xs, ys, 0, where=[y >= 0 for y in ys],
                 color="#2ecc71", alpha=0.15, interpolate=True)
ax1.fill_between(xs, ys, 0, where=[y < 0 for y in ys],
                 color="#e74c3c", alpha=0.15, interpolate=True)
ax1.axhline(0, color="#555", linewidth=0.8, linestyle="--")
ax1.set_title("Cumulative PnL (chronological)", color="white", fontsize=13, pad=10)
ax1.set_xlabel("Trade #", color="#aaa")
ax1.set_ylabel("USDT", color="#aaa")
ax1.tick_params(colors="#aaa")
for spine in ax1.spines.values():
    spine.set_color("#333")
ax1.grid(True, color="#222", linestyle="--", linewidth=0.5)

# ---- 2. Summary stats card ----
ax2 = plt.subplot2grid((3, 3), (0, 2))
ax2.set_facecolor("#161a23")
ax2.axis("off")
txt = [
    ("TOTAL", ""),
    ("Trades", f"{s_all['n']}"),
    ("Wins / Losses", f"{s_all['w']} / {s_all['l']}"),
    ("Win rate", f"{s_all['wr']:.1f}%"),
    ("Net PnL", f"${s_all['pnl']:+.2f}"),
    ("Profit factor", f"{s_all['pf']:.2f}"),
    ("Avg win / loss", f"${s_all['avg_win']:+.2f} / ${s_all['avg_loss']:+.2f}"),
    ("Best / Worst", f"${s_all['best']:+.2f} / ${s_all['worst']:+.2f}"),
    ("Avg duration", f"{s_all['avg_dur']:.1f} min"),
]
y = 0.95
for label, val in txt:
    if val == "":
        ax2.text(0.03, y, label, color="#4da6ff", fontsize=13, fontweight="bold",
                 transform=ax2.transAxes)
    else:
        ax2.text(0.03, y, label, color="#aaa", fontsize=10, transform=ax2.transAxes)
        ax2.text(0.97, y, val, color="white", fontsize=10, fontweight="bold",
                 ha="right", transform=ax2.transAxes)
    y -= 0.11
ax2.set_title("Summary", color="white", fontsize=13, pad=10, loc="left")

# ---- 3. Per-symbol bar chart: PnL ----
ax3 = plt.subplot2grid((3, 3), (1, 0))
ax3.set_facecolor("#161a23")
symbols = ["SOL-USDT", "ZEC-USDT", "TOTAL"]
pnls = [s_sol["pnl"], s_zec["pnl"], s_all["pnl"]]
bar_colors = ["#2ecc71" if p >= 0 else "#e74c3c" for p in pnls]
bars = ax3.bar(symbols, pnls, color=bar_colors, edgecolor="white", linewidth=0.5)
for b, p in zip(bars, pnls):
    ax3.text(b.get_x() + b.get_width() / 2, b.get_height(),
             f"${p:+.2f}", ha="center",
             va="bottom" if p >= 0 else "top",
             color="white", fontsize=10, fontweight="bold")
ax3.axhline(0, color="#555", linewidth=0.8)
ax3.set_title("Net PnL by Symbol", color="white", fontsize=12, pad=10)
ax3.set_ylabel("USDT", color="#aaa")
ax3.tick_params(colors="#aaa")
for spine in ax3.spines.values():
    spine.set_color("#333")
ax3.grid(True, axis="y", color="#222", linestyle="--", linewidth=0.5)

# ---- 4. Win rate per symbol ----
ax4 = plt.subplot2grid((3, 3), (1, 1))
ax4.set_facecolor("#161a23")
wrs = [s_sol["wr"], s_zec["wr"], s_all["wr"]]
bars = ax4.bar(symbols, wrs, color=["#4da6ff", "#a855f7", "#f59e0b"],
               edgecolor="white", linewidth=0.5)
for b, w in zip(bars, wrs):
    ax4.text(b.get_x() + b.get_width() / 2, b.get_height() + 1,
             f"{w:.1f}%", ha="center", color="white", fontsize=10, fontweight="bold")
ax4.axhline(50, color="#555", linewidth=0.8, linestyle="--")
ax4.set_ylim(0, 100)
ax4.set_title("Win Rate by Symbol", color="white", fontsize=12, pad=10)
ax4.set_ylabel("%", color="#aaa")
ax4.tick_params(colors="#aaa")
for spine in ax4.spines.values():
    spine.set_color("#333")
ax4.grid(True, axis="y", color="#222", linestyle="--", linewidth=0.5)

# ---- 5. W/L count per symbol ----
ax5 = plt.subplot2grid((3, 3), (1, 2))
ax5.set_facecolor("#161a23")
sym_labels = ["SOL-USDT", "ZEC-USDT"]
w_counts = [s_sol["w"], s_zec["w"]]
l_counts = [s_sol["l"], s_zec["l"]]
x_pos = range(len(sym_labels))
ax5.bar(x_pos, w_counts, color="#2ecc71", edgecolor="white", linewidth=0.5, label="Wins")
ax5.bar(x_pos, l_counts, bottom=w_counts, color="#e74c3c", edgecolor="white",
        linewidth=0.5, label="Losses")
for i, (w, l) in enumerate(zip(w_counts, l_counts)):
    if w:
        ax5.text(i, w / 2, str(w), ha="center", va="center", color="white",
                 fontsize=11, fontweight="bold")
    if l:
        ax5.text(i, w + l / 2, str(l), ha="center", va="center", color="white",
                 fontsize=11, fontweight="bold")
ax5.set_xticks(list(x_pos))
ax5.set_xticklabels(sym_labels)
ax5.set_title("Wins vs Losses", color="white", fontsize=12, pad=10)
ax5.set_ylabel("Trades", color="#aaa")
ax5.tick_params(colors="#aaa")
ax5.legend(facecolor="#161a23", edgecolor="#333", labelcolor="white", fontsize=9)
for spine in ax5.spines.values():
    spine.set_color("#333")
ax5.grid(True, axis="y", color="#222", linestyle="--", linewidth=0.5)

# ---- 6. Trade-by-trade PnL bars ----
ax6 = plt.subplot2grid((3, 3), (2, 0), colspan=3)
ax6.set_facecolor("#161a23")
labels = [f"{t[0].split('-')[0]} {t[1][0].upper()}" for t in trades]
pnl_vals = [t[4] for t in trades]
bar_cols = ["#2ecc71" if p > 0 else "#e74c3c" for p in pnl_vals]
edge_cols = ["#f59e0b" if t[5] == "drift" else "white" for t in trades]
bars = ax6.bar(range(len(trades)), pnl_vals, color=bar_cols,
               edgecolor=edge_cols, linewidth=1.5)
for i, (b, p) in enumerate(zip(bars, pnl_vals)):
    ax6.text(b.get_x() + b.get_width() / 2, b.get_height(),
             f"${p:+.1f}", ha="center",
             va="bottom" if p >= 0 else "top",
             color="white", fontsize=8)
ax6.set_xticks(range(len(trades)))
ax6.set_xticklabels(labels, rotation=0, fontsize=9)
ax6.axhline(0, color="#555", linewidth=0.8)
ax6.set_title("Per-Trade PnL (orange border = drift exit / SL hit)",
              color="white", fontsize=12, pad=10)
ax6.set_ylabel("USDT", color="#aaa")
ax6.tick_params(colors="#aaa")
for spine in ax6.spines.values():
    spine.set_color("#333")
ax6.grid(True, axis="y", color="#222", linestyle="--", linewidth=0.5)

plt.tight_layout(rect=(0, 0, 1, 0.96))
out = "docs/reports/scalping_24h_2026-04-12.png"
plt.savefig(out, dpi=150, facecolor="#0f1117", bbox_inches="tight")
print(f"Saved: {out}")
print()
print(f"SOL  n={s_sol['n']} wr={s_sol['wr']:.1f}% pnl=${s_sol['pnl']:+.2f} pf={s_sol['pf']:.2f}")
print(f"ZEC  n={s_zec['n']} wr={s_zec['wr']:.1f}% pnl=${s_zec['pnl']:+.2f} pf={s_zec['pf']:.2f}")
print(f"ALL  n={s_all['n']} wr={s_all['wr']:.1f}% pnl=${s_all['pnl']:+.2f} pf={s_all['pf']:.2f}")
