"""Per-time-window net for the live regime_mr basket at a fixed sizing.

Slices the REAL pooled trade stream into consecutive 4-week and 13-week
(~3-month) blocks so we see actual per-period profit + variation, not a
26-week average divided down. Same honest engine + liquidation model.

Usage: python per_period.py [margin] [leverage] [coins_csv]
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
import common as K
from btengine import simulate

PARAMS = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
              sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14)
LIQ_BUFFER = 0.005
TF = "15m"


def stream(coins):
    fn = K.SL.REGISTRY["regime_mr"]
    rows = []
    for c in coins:
        df = K.load(c, TF)
        for t in simulate(df, fn(df, side="both", **PARAMS), K.LIGHTER, K.RISK, K.TF_MIN[TF]):
            r = t.side * (t.exit_price - t.entry_price) / t.entry_price
            rows.append((pd.Timestamp(t.entry_time), r, t.mae_frac))
    rows.sort(key=lambda x: x[0])
    return rows


def pnl_array(rows, M, L):
    r = np.array([x[1] for x in rows]); mae = np.array([x[2] for x in rows])
    liq_frac = (1.0 / L) * (1 - LIQ_BUFFER)
    return np.where(mae >= liq_frac, -float(M), r * (M * L))


def blocks(rows, pnl, t0, t1, weeks_per):
    """Net + trade count per consecutive window of `weeks_per` weeks."""
    edges = []
    cur = t0
    step = pd.Timedelta(weeks=weeks_per)
    while cur < t1:
        edges.append(cur); cur += step
    edges.append(t1 + pd.Timedelta(seconds=1))
    times = np.array([x[0].value for x in rows])
    out = []
    for k in range(len(edges) - 1):
        m = (times >= edges[k].value) & (times < edges[k + 1].value)
        out.append((edges[k].date(), int(m.sum()), float(pnl[m].sum())))
    return out


def main():
    M = float(sys.argv[1]) if len(sys.argv) > 1 else 500.0
    L = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    coins = sys.argv[3].split(",") if len(sys.argv) > 3 else ["SOL", "ETH", "ZEC", "HYPE"]
    rows = stream(coins)
    pnl = pnl_array(rows, M, L)
    t0, t1 = rows[0][0], rows[-1][0]
    wk = (t1 - t0).total_seconds() / (7 * 86400)
    print(f"\n### regime_mr per-period | {coins} | margin ${M:.0f} x {L} = ${M*L:.0f} notional")
    print(f"period {t0.date()} -> {t1.date()} ({wk:.1f} wk) | {len(rows)} trades | total net ${pnl.sum():,.0f}")

    for label, w in (("4-WEEK BLOCKS", 4), ("13-WEEK (~3mo) BLOCKS", 13)):
        b = blocks(rows, pnl, t0, t1, w)
        nets = [x[2] for x in b]
        print(f"\n{label}:")
        for start, n, net in b:
            print(f"  from {start}  trades={n:>4}  net=${net:>9,.0f}")
        full = [x for x in b if x[1] > 0]
        nets_full = [x[2] for x in full]
        print(f"  -> avg ${np.mean(nets_full):,.0f} | median ${np.median(nets_full):,.0f} | "
              f"best ${max(nets_full):,.0f} | worst ${min(nets_full):,.0f}  (per {w}wk)")


if __name__ == "__main__":
    main()
