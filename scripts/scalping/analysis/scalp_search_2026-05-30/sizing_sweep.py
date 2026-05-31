"""Margin x leverage sizing sweep for the LIVE regime_mr scalper.

Reuses the honest no-lookahead engine (btengine.simulate) and the SAME fixed-
notional honest-liquidation model as basket_validate.py. Strategy logic is
UNCHANGED — we only vary position sizing.

Model (per pooled trade, fractional return r and max adverse excursion mae):
  notional N = margin M * leverage L
  liq_frac   = (1/L) * (1 - 0.005)     # ~maintenance buffer
  pnl        = -M               if mae >= liq_frac   (liquidated: lose full margin)
               r * N            otherwise            (normal: notional * return)
  equity     = cumsum(pnl) on the time-ordered pooled stream (fixed sizing, no compounding)

Liquidation is the ONLY thing leverage changes: it converts deep-MAE trades
(including MR trades that would have recovered to a WIN) into total-margin losses.

Usage: python sizing_sweep.py [coins_csv]   (default = live 4-coin basket)
"""
from __future__ import annotations
import sys
import numpy as np
import common as K
from btengine import simulate

PARAMS = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
              sl_atr=2.0, tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14)
MARGINS = [50, 100, 150, 200, 250, 500]
LEVS = [5, 10, 15, 20, 30]
LIQ_BUFFER = 0.005
TF = "15m"


def pooled_stream(coins, costs):
    """Build the time-ordered pooled (entry_time, r, mae) stream for the basket."""
    fn = K.SL.REGISTRY["regime_mr"]
    rows = []
    for c in coins:
        df = K.load(c, TF)
        tr = simulate(df, fn(df, side="both", **PARAMS), costs, K.RISK, K.TF_MIN[TF])
        for t in tr:
            r = t.side * (t.exit_price - t.entry_price) / t.entry_price
            rows.append((t.entry_time, r, t.mae_frac))
    rows.sort(key=lambda x: x[0])
    return rows


def combo(rows, M, L):
    r = np.array([x[1] for x in rows]); mae = np.array([x[2] for x in rows])
    N = M * L
    liq_frac = (1.0 / L) * (1 - LIQ_BUFFER)
    liq_mask = mae >= liq_frac
    pnl = np.where(liq_mask, -float(M), r * N)
    eq = np.cumsum(pnl)
    base = np.concatenate([[0.0], eq])
    dd = (np.maximum.accumulate(base) - base).max()
    wins = pnl[pnl > 0]; losses = pnl[pnl < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    return dict(M=M, L=L, N=N, net=pnl.sum(), pf=pf,
                wr=100 * len(wins) / len(pnl), dd=dd, n=len(pnl),
                avg=pnl.sum() / len(pnl), worst=pnl.min(),
                liq=int(liq_mask.sum()), liqd=100 * liq_frac,
                rr=(pnl.sum() / dd) if dd > 0 else float("inf"))


def main():
    coins = sys.argv[1].split(",") if len(sys.argv) > 1 else ["SOL", "ETH", "ZEC", "HYPE"]
    base_df = K.load(coins[0], TF)
    weeks = K.weeks_of(base_df, TF)
    print(f"\n### regime_mr SIZING SWEEP | {TF} | coins={coins}")
    print(f"period {base_df.index[0].date()} -> {base_df.index[-1].date()} ({weeks:.1f} wk)")

    rows = pooled_stream(coins, K.LIGHTER)              # 0.05% realistic slip
    rows_hi = pooled_stream(coins, K.LIGHTER_HISLIP)    # 0.10% stress slip
    r = np.array([x[1] for x in rows])
    w = r[r > 0]; l = r[r < 0]
    base_pf = w.sum() / -l.sum() if l.sum() < 0 else float("inf")
    print(f"pooled trades n={len(rows)} ({len(rows)/weeks:.0f}/wk)  base WR={100*len(w)/len(r):.0f}%  "
          f"base PF={base_pf:.2f}  maxMAE={100*max(x[2] for x in rows):.2f}%  "
          f"avgR={100*r.mean():+.3f}%")

    results = [combo(rows, M, L) for M in MARGINS for L in LEVS]
    hi = {(c["M"], c["L"]): c for c in (combo(rows_hi, M, L) for M in MARGINS for L in LEVS)}
    results.sort(key=lambda c: c["net"], reverse=True)

    print("\nRANKED BY NET PROFIT (0.05% slip).  net@2x = net$ under 0.10% slip stress.")
    print(f"{'rank':>4} {'margin':>7} {'lev':>4} {'notl$':>7} {'net$':>9} {'PF':>5} {'WR%':>5} "
          f"{'maxDD$':>8} {'net/DD':>7} {'avg$/t':>7} {'worst$':>8} {'liq#':>5} {'liqDist%':>8} {'net@2x$':>9}")
    for i, c in enumerate(results, 1):
        pf = "inf" if c["pf"] == float("inf") else f"{c['pf']:.2f}"
        rr = "inf" if c["rr"] == float("inf") else f"{c['rr']:.1f}"
        h = hi[(c["M"], c["L"])]
        print(f"{i:>4} {c['M']:>7.0f} {c['L']:>4}x {c['N']:>7.0f} {c['net']:>9.0f} {pf:>5} {c['wr']:>5.0f} "
              f"{c['dd']:>8.0f} {rr:>7} {c['avg']:>7.2f} {c['worst']:>8.0f} {c['liq']:>5} "
              f"{c['liqd']:>8.2f} {h['net']:>9.0f}")


if __name__ == "__main__":
    main()
