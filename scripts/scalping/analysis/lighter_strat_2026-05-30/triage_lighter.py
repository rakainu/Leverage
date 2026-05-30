"""Cross-instrument triage under Lighter zero-fee costs. For each family x TF x side,
run ONE sensible (untuned) param set across SOL/ETH/ZEC/HYPE on the full sample and
show per-coin PF + how many coins clear PF>=1.20. This directly answers "does the
idea work across coins, not just one lucky one" before any tuning.
"""
from __future__ import annotations
from common import load_coin, evalc, COINS, TF_MIN, LIGHTER
import strat_lib as S

DEFAULTS = {
    "range_fade":        dict(lookback=40, edge_frac=0.12, adx_max=30, sl_atr=1.5, tp_to="mid", max_bars=48, limit_atr=0.0),
    "failed_breakout":   dict(lookback=20, sl_atr=1.0, tp_atr=2.0, entry="market", max_bars=48),
    "sweep_reversal":    dict(lookback=20, sl_atr=1.0, tp_atr=2.0, entry="market", wick_atr=0.0, max_bars=48),
    "squeeze_expansion": dict(bb_len=20, sl_atr=1.5, tp_atr=3.0, min_squeeze=6, entry="market"),
    "reclaim_pullback":  dict(fast=20, slow=100, sl_atr=1.5, tp_atr=3.0, entry="market"),
    "mr_fade2":          dict(z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, adx_max=35, limit_atr=0.0),
}

def main():
    # preload
    data = {(c, tf): load_coin(c, tf) for c in COINS for tf in TF_MIN}
    print(f"{'family':17}{'tf':4}{'side':6}" + "".join(f"{c:>8}" for c in COINS) + f"{'avgPF':>8}{'>=1.2':>6}")
    print("-" * 86)
    for fam, fn in S.REGISTRY.items():
        params = DEFAULTS[fam]
        for tf in ["5m", "15m", "1h"]:
            for side in ["both", "long", "short"]:
                pfs = []; ns = []
                for c in COINS:
                    m = evalc(fn, data[(c, tf)], side, params, LIGHTER, tf)
                    pfs.append(m["profit_factor"]); ns.append(m["n"])
                shown = [(f"{p:.2f}" if p != float('inf') else "inf") for p in pfs]
                finite = [p for p in pfs if p != float('inf')]
                avg = sum(finite) / len(finite) if finite else 0
                cnt = sum(1 for p in pfs if p >= 1.20)
                # flag rows where most coins have enough trades AND avg PF promising
                flag = " <<" if (cnt >= 3 and avg >= 1.20 and min(ns) >= 15) else ""
                print(f"{fam:17}{tf:4}{side:6}" + "".join(f"{s:>8}" for s in shown) + f"{avg:>8.2f}{cnt:>6}{flag}")
    print("\n(<< = >=3 coins PF>=1.20, avgPF>=1.20, min 15 trades/coin — promising cross-instrument)")

if __name__ == "__main__":
    main()
