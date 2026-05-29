"""SL-curve probe: best configs sit at the SL=2.5 grid edge. Check whether wider stops
keep improving (edge-running = overfit risk) or peak at an interior optimum.
Lighter (zero-fee) only, both entry timings, on the winning ladder/ratio family.
"""
from __future__ import annotations
import sys
from dataclasses import replace
import pandas as pd
from replay import ExitParams, load_prices, run_replay, SIGNALS_CSV
from strategy import kpis

def split_oos(t, frac=0.30):
    if t.empty: return t, t
    cut = int(len(t)*(1-frac)); return t.iloc[:cut], t.iloc[cut:]

allsig = pd.read_csv(SIGNALS_CSV)
LADDER = (1.0, 2.0, 3.0); RATIOS = (0.34, 0.33, 0.33)
print(f"ladder={LADDER} ratios={RATIOS} be=True  Lighter zero-fee\n")
for sym in ["ZEC-USDT", "SOL-USDT"]:
    px = load_prices(sym); s = allsig[allsig.symbol == sym]
    print(f"== {sym} ==")
    print(f"{'timing':7}{'sl_atr':>7}{'n':>6}{'net':>9}{'pf':>7}{'oos_n':>7}{'oos_net':>9}{'oos_pf':>7}{'wr':>6}{'dd':>8}")
    for timing in ["signal", "retest"]:
        for sl in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
            p = replace(ExitParams(), sl_atr=sl, tp1_atr=LADDER[0], tp2_atr=LADDER[1],
                        tp3_atr=LADDER[2], r1=RATIOS[0], r2=RATIOS[1], r3=RATIOS[2],
                        be_after_tp1=True, commission_pct=0.0)
            t = run_replay(s, px, p, entry_timing=timing)
            if t.empty: continue
            k = kpis(t); _, oos = split_oos(t); ok = kpis(oos) if not oos.empty else {"n":0,"net_pnl":0,"profit_factor":0}
            print(f"{timing:7}{sl:>7}{k['n']:>6}{k['net_pnl']:>9.0f}{k['profit_factor']:>7.2f}"
                  f"{ok['n']:>7}{ok['net_pnl']:>9.0f}{ok['profit_factor']:>7.2f}{k['win_rate']:>6.0%}{k['max_dd']:>8.0f}")
    print()
