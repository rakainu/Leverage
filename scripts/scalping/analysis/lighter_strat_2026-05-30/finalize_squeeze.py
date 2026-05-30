"""Final validation of the squeeze compression->expansion candidate as a 4-coin
PORTFOLIO. Builds the merged portfolio equity curve (shared $1000, 1% risk/trade,
trades ordered by exit time) and reports the full metric set. Tests Lighter zero-fee
vs higher slippage vs BloFin fees, plus pooled IS/OOS and 3-fold walk-forward.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
from common import load_coin, COINS, TF_MIN, LIGHTER, LIGHTER_HISLIP, BLOFIN, RISK, split_is_oos, walk_forward_folds
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import simulate  # noqa: E402
from strat_lib import squeeze_expansion as SQ

TF = "1h"
CAND = dict(side="both", bb_len=20, kc_mult=1.5, min_squeeze=10, sl_atr=1.5, tp_atr=3.0, trail=True, entry="market")

def coin_trades(df, costs):
    p = {k: v for k, v in CAND.items() if k != "side"}
    return simulate(df, SQ(df, side=CAND["side"], **p), costs, RISK, TF_MIN[TF])

def portfolio_metrics(dfs: dict, costs, risk_frac=0.01, start=1000.0):
    """Merge all coins' trades by exit time onto one shared compounding equity."""
    recs = []
    for c, df in dfs.items():
        for t in coin_trades(df, costs):
            recs.append((t.exit_time, t.r_multiple))
    recs.sort(key=lambda x: x[0])
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        pnl = r * risk_frac * eq
        eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    if len(pnls) == 0:
        return None
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    # worst losing streak
    streak = worst = 0; cur = worstusd = 0.0
    for p in pnls:
        if p < 0: streak += 1; cur += p; worst = max(worst, streak); worstusd = min(worstusd, cur)
        else: streak = 0; cur = 0.0
    rs = np.array([r for _, r in recs])
    return dict(n=len(pnls), pf=pf, wr=(pnls > 0).mean()*100, avg_r=rs.mean(),
                net=pnls.sum(), net_pct=pnls.sum()/start*100, max_dd=dd,
                worst_streak=worst, worst_streak_usd=worstusd, final=eq,
                t_stat=rs.mean()/(rs.std(ddof=1)/np.sqrt(len(rs))))

def show(tag, m):
    if m is None:
        print(f"  {tag:22} (no trades)"); return
    pf = 'inf' if m['pf']==float('inf') else f"{m['pf']:.2f}"
    print(f"  {tag:22} n={m['n']:>3} PF={pf:>5} WR={m['wr']:>3.0f}% avgR={m['avg_r']:+.3f} "
          f"net={m['net']:+6.0f}({m['net_pct']:+.0f}%) maxDD={m['max_dd']:.1f}% "
          f"streak={m['worst_streak']}({m['worst_streak_usd']:.0f}) t={m['t_stat']:.2f}")

def main():
    full = {c: load_coin(c, TF) for c in COINS}
    isd = {c: split_is_oos(full[c], 0.70)[0] for c in COINS}
    oosd = {c: split_is_oos(full[c], 0.70)[1] for c in COINS}
    print(f"CANDIDATE: squeeze compression->expansion, 4-coin portfolio, 1h\n  {CAND}\n")
    print("PORTFOLIO (merged equity, $1000, 1% risk/trade):")
    show("Lighter slip .05", portfolio_metrics(full, LIGHTER))
    show("Lighter slip .10", portfolio_metrics(full, LIGHTER_HISLIP))
    show("BloFin fees", portfolio_metrics(full, BLOFIN))
    print("\nLighter zero-fee — IS / OOS split:")
    show("IS (70%)", portfolio_metrics(isd, LIGHTER))
    show("OOS (30%)", portfolio_metrics(oosd, LIGHTER))
    print("\nLighter zero-fee — 3-fold walk-forward (portfolio test slices):")
    foldmap = {c: walk_forward_folds(full[c], 3) for c in COINS}
    for k in range(3):
        teslice = {c: foldmap[c][k][1] for c in COINS}
        show(f"fold {k} test", portfolio_metrics(teslice, LIGHTER))

if __name__ == "__main__":
    main()
