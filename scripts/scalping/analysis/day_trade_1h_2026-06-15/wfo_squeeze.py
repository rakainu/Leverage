"""Anchored walk-forward optimization (WFO) — the honest test.

No more tuning on all the data and checking a tail. Here:
  * Split the timeline into sequential OOS windows.
  * For each window: grid-search the BEST config on data STRICTLY BEFORE it
    (expanding in-sample), then trade ONLY that next unseen window with the
    chosen config. Selection never touches the test data.
  * Stitch all OOS segments -> one equity curve = what this strategy would have
    REALLY produced trading forward, re-tuning as it goes.
  * Compare to a fixed-config benchmark (no tuning) and to a buy&hold-agnostic
    'always trade base' line, so we see if the tuning even helps.
  * Run the WHOLE thing on dev coins AND on out-of-universe coins.
  * Isolate ZEC: does the dev edge survive without the star?
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tune_squeeze import (load_1h, make_signals, bt, TF_MIN, LIGHTER, BLOFIN,  # noqa: E402
                          DEV_COINS, OOS_COINS, DEV_DIR, OOS_DIR, avail)

# inner optimization grid (kept modest to avoid over-search)
GRID = [dict(min_squeeze=ms, sl_atr=sl, tp_atr=tp)
        for ms, sl, tp in itertools.product([8, 10, 12], [1.5, 2.0], [2.5, 3.0, 3.5])]
FIXED = dict(min_squeeze=12, sl_atr=1.5, tp_atr=3.0)


def trades_in(dfs, cfg, costs, lo=None, hi=None):
    """All trades (exit_time, r) whose ENTRY falls in [lo,hi); signals computed on full df (no look-ahead: indicators only see past)."""
    out = []
    for c, df in dfs.items():
        sigs = make_signals(df, cfg)
        risk = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20, liq_buffer=2.5, compounding=True)
        for t in bt.simulate(df, sigs, costs, risk, TF_MIN):
            et = t.entry_time
            if (lo is None or et >= lo) and (hi is None or et < hi):
                out.append((t.exit_time, t.r_multiple))
    return out


def metrics_from_r(recs, rf=0.01, start=1000.0):
    if not recs:
        return None
    recs = sorted(recs, key=lambda x: x[0])
    eq = start; curve = [start]; pnls = []
    for _, r in recs:
        pnl = r * rf * eq; eq += pnl; pnls.append(pnl); curve.append(eq)
    pnls = np.array(pnls); curve = np.array(curve)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    peak = np.maximum.accumulate(curve); dd = ((peak - curve) / peak).max() * 100
    rs = np.array([r for _, r in recs])
    t_stat = rs.mean() / (rs.std(ddof=1) / np.sqrt(len(rs))) if len(rs) > 1 else 0.0
    return dict(n=len(pnls), pf=pf, wr=(pnls > 0).mean() * 100, avg_r=rs.mean(),
                net_pct=(eq - start) / start * 100, max_dd=dd, t=t_stat, final=eq)


def opt_on(dfs, lo, hi, costs):
    """Best config (by PF, n>=20) trained on entries in [lo,hi)."""
    best, best_pf = None, -1
    for cfg in GRID:
        m = metrics_from_r(trades_in(dfs, cfg, costs, lo, hi))
        if m and m["n"] >= 20 and m["pf"] > best_pf:
            best, best_pf = cfg, m["pf"]
    return best or FIXED


def wfo(dfs, n_oos=6, is_frac0=0.40, costs=LIGHTER, label=""):
    idx_min = min(df.index.min() for df in dfs.values())
    idx_max = max(df.index.max() for df in dfs.values())
    span = idx_max - idx_min
    is_end0 = idx_min + span * is_frac0
    cuts = [is_end0 + (idx_max - is_end0) * k / n_oos for k in range(n_oos + 1)]
    stitched, fixed_stitched, picks = [], [], []
    for k in range(n_oos):
        oos_lo, oos_hi = cuts[k], cuts[k + 1]
        cfg = opt_on(dfs, None, oos_lo, costs)            # train strictly before OOS window
        picks.append((cfg["min_squeeze"], cfg["sl_atr"], cfg["tp_atr"]))
        stitched += trades_in(dfs, cfg, costs, oos_lo, oos_hi)
        fixed_stitched += trades_in(dfs, FIXED, costs, oos_lo, oos_hi)
    mt = metrics_from_r(stitched); mf = metrics_from_r(fixed_stitched)
    print(f"\n--- WFO {label} | {n_oos} OOS windows, expanding IS from {is_frac0:.0%} ---")
    def show(tag, m):
        if not m: print(f"  {tag:<22} (no trades)"); return
        pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
        print(f"  {tag:<22} n={m['n']:>4} PF={pf:>5} WR={m['wr']:3.0f}% net={m['net_pct']:>+6.0f}% "
              f"DD={m['max_dd']:4.1f}% t={m['t']:+.2f}")
    show("WFO re-tuned (OOS)", mt)
    show("fixed cfg (OOS)", mf)
    print(f"  configs chosen per window: {picks}")
    return mt


def main():
    dev = {c: load_1h(c, DEV_DIR) for c in DEV_COINS}
    print("=" * 78)
    print("ANCHORED WALK-FORWARD — out-of-sample stitched equity (the real test)")
    print("=" * 78)

    wfo(dev, label="DEV 4-coin (Lighter)")
    wfo(dev, costs=BLOFIN, label="DEV 4-coin (BloFin fees)")

    # isolate ZEC dependence
    no_zec = {c: dev[c] for c in DEV_COINS if c != "ZEC"}
    wfo(no_zec, label="DEV minus ZEC (does edge survive without the star?)")
    print("\n  per-coin full-window WFO:")
    for c in DEV_COINS:
        wfo({c: dev[c]}, n_oos=4, label=f"{c} only")

    # out-of-universe
    oc = avail(OOS_COINS, OOS_DIR)
    if oc:
        oos = {c: load_1h(c, OOS_DIR) for c in oc}
        wfo(oos, label=f"OUT-OF-UNIVERSE {oc} (Lighter)")
        wfo(oos, costs=BLOFIN, label=f"OUT-OF-UNIVERSE {oc} (BloFin fees)")


if __name__ == "__main__":
    main()
