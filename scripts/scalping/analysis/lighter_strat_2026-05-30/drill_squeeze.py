"""Drill the leading squeeze configs: per-coin FULL/OOS (n, WR, PF, avgR, maxDD),
pooled profit concentration, trail-vs-notrail, and pooled monthly PnL. Decide
honestly whether the edge is robust cross-instrument or carried by one coin / a
few fat-tail trades.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
from common import load_coin, COINS, TF_MIN, LIGHTER, RISK, split_is_oos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics  # noqa: E402
from strat_lib import squeeze_expansion as SQ

TF = "1h"
CONFIGS = {
    "A both sq10 trail":   dict(side="both", bb_len=20, kc_mult=1.5, min_squeeze=10, sl_atr=1.5, tp_atr=3.0, trail=True, entry="market"),
    "A' both sq10 NOtrail":dict(side="both", bb_len=20, kc_mult=1.5, min_squeeze=10, sl_atr=1.5, tp_atr=3.0, trail=False, entry="market"),
    "B both sq6 trail":    dict(side="both", bb_len=20, kc_mult=1.5, min_squeeze=6, sl_atr=2.0, tp_atr=3.0, trail=True, entry="market"),
    "C short sq4 trail":   dict(side="short", bb_len=20, kc_mult=1.5, min_squeeze=4, sl_atr=1.5, tp_atr=3.0, trail=True, entry="market"),
}

def trades_of(df, cfg):
    p = {k: v for k, v in cfg.items() if k != "side"}
    return simulate(df, SQ(df, side=cfg["side"], **p), LIGHTER, RISK, TF_MIN[TF])

def main():
    data = {c: load_coin(c, TF) for c in COINS}
    splits = {c: split_is_oos(data[c], 0.70) for c in COINS}
    for name, cfg in CONFIGS.items():
        print(f"\n{'='*92}\n{name}  {cfg}\n{'='*92}")
        all_r = []; all_pnl_idx = []
        print(f"  {'coin':5} {'FULL n/WR/PF/avgR/DD':38} {'OOS n/WR/PF/avgR/DD':38}")
        for c in COINS:
            trf = trades_of(data[c], cfg); tro = trades_of(splits[c][1], cfg)
            mf = metrics(trf, 1000.0); mo = metrics(tro, 1000.0)
            all_r.extend([t.r_multiple for t in trf])
            for t in trf:
                all_pnl_idx.append((t.exit_time, t.r_multiple))
            def s(m):
                pf = m['profit_factor']; pfs='inf' if pf==float('inf') else f"{pf:.2f}"
                return f"n{m['n']:>3} WR{m['win_rate']:>3.0f} PF{pfs:>5} R{m['avg_r']:+.2f} DD{m['max_dd_pct']:>4.1f}"
            print(f"  {c:5} {s(mf):38} {s(mo):38}")
        r = np.array(all_r)
        wins = np.sort(r[r > 0])[::-1]
        pooled_pf = wins.sum() / -r[r < 0].sum() if (r < 0).any() else float('inf')
        print(f"  POOLED n={len(r)} PF={pooled_pf:.2f} meanR={r.mean():+.3f} WR={(r>0).mean()*100:.0f}%  "
              f"top3wins={wins[:3].sum()/wins.sum()*100:.0f}% of gross  t-stat={r.mean()/(r.std(ddof=1)/np.sqrt(len(r))):.2f}")
        # pooled monthly
        s = pd.Series([x[1] for x in all_pnl_idx], index=[x[0] for x in all_pnl_idx]).sort_index()
        m = s.groupby(pd.Grouper(freq="ME")).sum()
        print("  pooled monthly R: " + " ".join(f"{ts.strftime('%m')}:{v:+.1f}" for ts, v in m.items()))

if __name__ == "__main__":
    main()
