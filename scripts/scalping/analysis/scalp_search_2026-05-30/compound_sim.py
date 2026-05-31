"""Compounding trajectory for the live regime_mr basket — for comparison vs the
flat-sizing sweep. Same honest engine + liquidation model.

Compounding model (fixed-fractional, the faithful version of '$400 margin on a
$2000 account, 15x'): each trade's margin = margin_frac * CURRENT equity, so the
bet grows as the account grows. notional = margin * leverage.
  margin_frac default = 400/2000 = 0.20 per position (4 concurrent ~= 80% margin).
  per-trade equity update (time-ordered pooled stream, ~14min holds so entry
  order ~= close order):
     liquidated (mae >= (1/L)*0.995):  equity *= (1 - margin_frac)   # lose the margin
     else:                             equity *= (1 + r * margin_frac * L)
Reports flat vs compounded final equity, multiple, max drawdown %, and the
month-by-month equity curve. NOTE margin_frac*L = exposure-to-equity per position
(=3.0 here) — aggressive; the drawdown reflects that honestly.

Usage: python compound_sim.py [equity0] [margin_frac] [leverage] [coins_csv]
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


def run(rows, eq0, frac, L, compound):
    liq_frac = (1.0 / L) * (1 - LIQ_BUFFER)
    eq = eq0
    fixed_margin = frac * eq0
    peak = eq0; maxdd = 0.0
    curve = []   # (time, equity)
    ruin = False
    for ts, r, mae in rows:
        margin = frac * eq if compound else fixed_margin
        notional = margin * L
        if mae >= liq_frac:
            pnl = -margin
        else:
            pnl = r * notional
        eq += pnl
        if eq <= 0:
            ruin = True; eq = 0.0; curve.append((ts, eq)); break
        peak = max(peak, eq)
        maxdd = max(maxdd, (peak - eq) / peak)
        curve.append((ts, eq))
    return eq, maxdd, ruin, curve


def monthly(curve, eq0, t0):
    """Equity at the end of each ~4-week block."""
    pts = []
    edge = t0 + pd.Timedelta(weeks=4)
    last_eq = eq0
    for ts, eq in curve:
        while ts >= edge:
            pts.append((edge.date(), last_eq)); edge += pd.Timedelta(weeks=4)
        last_eq = eq
    pts.append(("final", last_eq))
    return pts


def main():
    eq0 = float(sys.argv[1]) if len(sys.argv) > 1 else 2000.0
    frac = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20
    L = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    coins = sys.argv[4].split(",") if len(sys.argv) > 4 else ["SOL", "ETH", "ZEC", "HYPE"]
    rows = stream(coins)
    t0 = rows[0][0]; wk = (rows[-1][0] - t0).total_seconds() / (7 * 86400)
    print(f"\n### regime_mr COMPOUNDING | {coins} | start ${eq0:.0f} | margin {frac*100:.0f}% of equity/position x {L}")
    print(f"period {t0.date()} -> {rows[-1][0].date()} ({wk:.1f} wk) | {len(rows)} trades | "
          f"exposure/position = {frac*L:.1f}x equity")

    f_eq, f_dd, _, _ = run(rows, eq0, frac, L, compound=False)
    c_eq, c_dd, c_ruin, c_curve = run(rows, eq0, frac, L, compound=True)

    print(f"\nFLAT sizing (margin fixed at ${frac*eq0:.0f}):")
    print(f"  final ${f_eq:,.0f}   net ${f_eq-eq0:,.0f}   x{f_eq/eq0:.1f}   maxDD {100*f_dd:.1f}%")
    print(f"COMPOUNDED (margin = {frac*100:.0f}% of growing equity):")
    if c_ruin:
        print(f"  *** RUIN — account hit $0 ***")
    print(f"  final ${c_eq:,.0f}   net ${c_eq-eq0:,.0f}   x{c_eq/eq0:.1f}   maxDD {100*c_dd:.1f}%")

    print("\nCOMPOUNDED equity, end of each 4-week block:")
    for label, eq in monthly(c_curve, eq0, t0):
        print(f"  {str(label):>10}  ${eq:,.0f}")


if __name__ == "__main__":
    main()
