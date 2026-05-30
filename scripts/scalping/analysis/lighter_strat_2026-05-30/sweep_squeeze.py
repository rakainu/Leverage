"""Tune squeeze_expansion on 1h with ANTI-OVERFIT discipline:
  - One param set is tuned on POOLED in-sample data across SOL/ETH/ZEC/HYPE
    (not per-coin), so it can't curve-fit any single coin.
  - Ranked by pooled IS profit factor (R-multiple pooled across coins) with a
    minimum pooled-trade guard.
  - Top configs are validated on each coin's OOS slice + a pooled 3-fold
    walk-forward. A config only matters if it holds OOS on MULTIPLE coins and a
    majority of WF folds are positive.
All under Lighter zero-fee costs (slippage still applied).
"""
from __future__ import annotations
import itertools, json, os
import numpy as np
from common import load_coin, COINS, TF_MIN, LIGHTER, RISK, split_is_oos, walk_forward_folds
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import simulate  # noqa: E402
from strat_lib import squeeze_expansion as SQ

TF = "1h"

def rmults(df, side, params, costs):
    tr = simulate(df, SQ(df, side=side, **params), costs, RISK, TF_MIN[TF])
    return np.array([t.r_multiple for t in tr])

def pooled_pf(rs):
    if len(rs) == 0:
        return 0.0
    g = rs[rs > 0].sum(); l = -rs[rs < 0].sum()
    return g / l if l > 0 else float("inf")

def main():
    data = {c: load_coin(c, TF) for c in COINS}
    splits = {c: split_is_oos(data[c], 0.70) for c in COINS}
    folds = {c: walk_forward_folds(data[c], 3) for c in COINS}

    grid = dict(bb_len=[20, 30], kc_mult=[1.0, 1.5], min_squeeze=[4, 6, 10],
                sl_atr=[1.5, 2.0, 2.5], tp_atr=[2.0, 3.0, 4.0], trail=[False, True])
    keys = list(grid)
    rows = []
    for side in ["both", "short", "long"]:
        for combo in itertools.product(*[grid[k] for k in keys]):
            p = dict(zip(keys, combo)); p["entry"] = "market"
            is_r = np.concatenate([rmults(splits[c][0], side, p, LIGHTER) for c in COINS]) \
                if True else np.array([])
            if len(is_r) < 120:   # ~30 trades/coin minimum pooled
                continue
            rows.append((side, p, pooled_pf(is_r), is_r.mean(), len(is_r)))
    rows.sort(key=lambda r: r[2], reverse=True)

    print(f"Top pooled-IS configs (squeeze 1h, Lighter zero-fee), then OOS + WF:\n")
    for side, p, ispf, ismean, isn in rows[:12]:
        # per-coin FULL + OOS PF
        full = {c: pooled_pf(rmults(data[c], side, p, LIGHTER)) for c in COINS}
        oos = {c: pooled_pf(rmults(splits[c][1], side, p, LIGHTER)) for c in COINS}
        oos_r = np.concatenate([rmults(splits[c][1], side, p, LIGHTER) for c in COINS])
        # pooled WF: for each fold, pool test-slice R across coins
        wf = []
        for k in range(3):
            fr = np.concatenate([rmults(folds[c][k][1], side, p, LIGHTER) for c in COINS])
            wf.append(round(pooled_pf(fr), 2))
        pos_oos = sum(1 for v in oos.values() if v >= 1.0)
        ps = f"bb{p['bb_len']} kc{p['kc_mult']} sq{p['min_squeeze']} sl{p['sl_atr']} tp{p['tp_atr']} trail{int(p['trail'])}"
        print(f"side={side:5} [{ps}]")
        print(f"   IS pooled PF={ispf:.2f} (n={isn}, meanR={ismean:+.3f}) | OOS pooled PF={pooled_pf(oos_r):.2f} (n={len(oos_r)})")
        print(f"   FULL per-coin PF: " + " ".join(f"{c}={full[c]:.2f}" for c in COINS))
        print(f"   OOS  per-coin PF: " + " ".join(f"{c}={oos[c]:.2f}" for c in COINS) + f"  ({pos_oos}/4 pos)")
        print(f"   WF pooled test PFs: {wf}  ({sum(1 for x in wf if x>1.0)}/3 pos)\n")

    with open(os.path.join(os.path.dirname(__file__), "runs", "squeeze_sweep.json"), "w") as f:
        json.dump([{"side": s, "params": p, "is_pf": pf, "is_mean_r": mr, "is_n": n}
                   for s, p, pf, mr, n in rows[:40]], f, default=str, indent=1)

if __name__ == "__main__":
    main()
