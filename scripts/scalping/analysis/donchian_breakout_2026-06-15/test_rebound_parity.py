"""Parity: the live bridge module (lighter_bridge.rebound) must produce the EXACT
same signal bars as the validated backtest (mr_engine) on identical data.

If this passes, the deployed paper bridge trades the edge we actually validated.
"""
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "boost-bridge", "src")))

import mr_engine as M
import run_donchian as R
from lighter_bridge.rebound import prepare_rebound

# champion config
C = dict(mean_anchor="vwap", vwap_len=48, bb_mult=2.5, adx_max=20, trigger="reclaim",
         atr_min_pct=0.4, bb_len=20, adx_len=14, atr_len=14)

mism = 0
total_long = total_short = 0
for coin in R.UNIVERSE:
    df = R.load_1h(coin)  # 5m -> 1h, same as the backtest universe
    # backtest signals (mr_engine.prepare + _fade_side per row)
    cfg = M.Cfg(stop_cap_pct=R.STOP_CAPS, tf_minutes=60, **C)
    bt = M.prepare(df, cfg)
    bt_long = np.zeros(len(bt), dtype=bool); bt_short = np.zeros(len(bt), dtype=bool)
    for i, r in enumerate(bt.itertuples()):
        side, _ = M._fade_side(r, cfg)
        if side > 0: bt_long[i] = True
        elif side < 0: bt_short[i] = True
    # live module signals
    live = prepare_rebound(df, vwap_len=C["vwap_len"], bb_len=C["bb_len"], bb_mult=C["bb_mult"],
                           adx_len=C["adx_len"], adx_max=C["adx_max"], atr_period=C["atr_len"],
                           atr_min_pct=C["atr_min_pct"])
    ll = live["reb_long"].values; ls = live["reb_short"].values
    dl = int((bt_long != ll).sum()); ds = int((bt_short != ls).sum())
    total_long += int(bt_long.sum()); total_short += int(bt_short.sum())
    flag = "OK" if (dl == 0 and ds == 0) else f"MISMATCH long={dl} short={ds}"
    print(f"  {coin:<5} bt_long={int(bt_long.sum()):>3} bt_short={int(bt_short.sum()):>3} "
          f"live_long={int(ll.sum()):>3} live_short={int(ls.sum()):>3}  {flag}")
    mism += dl + ds

print(f"\n  total signals: long={total_long} short={total_short}")
print(f"  PARITY: {'PASS — live module == backtest, bar-for-bar' if mism == 0 else f'FAIL ({mism} mismatched bars)'}")
sys.exit(0 if mism == 0 else 1)
