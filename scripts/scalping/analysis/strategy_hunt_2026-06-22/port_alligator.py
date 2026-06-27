"""Faithful port of 'AI - Williams Alligator Strategy (ATR Stop-Loss)' (Pine v6).

Source: USER;eac34cc2... Long-only. Alligator = SMMA(hl2) at 13/8/5.
  entry  = crossover(lips, jaw)
  exit   = crossunder(lips, jaw)   (+ ATR(14)*2.0 protective stop)
Honest engine via eventsim. Default params; basket + 5m/15m/1h, Lighter + BloFin.
"""
from __future__ import annotations
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Costs, RiskCfg, rma, atr  # noqa: E402
from metrics import extended_metrics          # noqa: E402

sys.path.insert(0, HERE)
from eventsim import simulate_rules            # noqa: E402
from stage2b_basket import load_tf, basket_metrics, COINS, RISK  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
BLOFIN = Costs(taker_pct=0.06, maker_pct=0.02, slippage_pct=0.05, funding_pct_per_8h=0.01)
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}
fin = lambda x: 999.0 if (x == float("inf") or (isinstance(x, float) and x != x)) else x


def alligator_signals(df, jaw_len=13, teeth_len=8, lips_len=5, atr_p=14, atr_mult=2.0):
    hl2 = (df["High"] + df["Low"]) / 2.0
    jaw = rma(hl2, jaw_len)          # Pine smma == Wilder rma (sma seed)
    lips = rma(hl2, lips_len)
    pj, pl = jaw.shift(1), lips.shift(1)
    cross_up = (pl <= pj) & (lips > jaw)
    cross_dn = (pl >= pj) & (lips < jaw)
    return cross_up, cross_dn, atr(df, atr_p), atr_mult


def run_basket(dfs, costs, tfm):
    tbc, liq = {}, 0
    for c, d in dfs.items():
        eL, xL, a, mult = alligator_signals(d)
        tr = simulate_rules(d, entries_long=eL, exits_long=xL, atr_series=a, atr_mult=mult,
                            costs=costs, risk=RISK, tf_minutes=tfm)
        tbc[c] = tr
        liq += extended_metrics(tr, RISK.starting_equity, compounding=False)["liq_hits"]
    return tbc, liq


def main():
    print("Williams Alligator (faithful, Pine v6) — honest engine, default params (13/8/5, ATR14x2), long-only")
    for tf in ["5m", "15m", "1h"]:
        dfs = {c: load_tf(c, tf) for c in COINS}
        tbc, liq = run_basket(dfs, LIGHTER, TF_MIN[tf])
        m = basket_metrics(tbc, RISK.starting_equity)
        bf, _ = run_basket(dfs, BLOFIN, TF_MIN[tf])
        bm = basket_metrics(bf, RISK.starting_equity)
        cp = sum(1 for c in COINS if extended_metrics(tbc[c], RISK.starting_equity, compounding=False)["profit_factor"] > 1.0)
        if m is None:
            print(f"  {tf:<4} no trades"); continue
        print(f"  {tf:<4} n={m['n']:>4} PF={fin(m['pf']):.2f} WR={m['wr']:>3.0f}% payoff={fin(m['payoff']):.2f} "
              f"net={m['net_pct']:>+6.0f}% DD={m['maxdd']:>3.0f}% liq={liq} coins+={cp}/8 | "
              f"BloFin PF={fin(bm['pf']):.2f} net={bm['net_pct']:>+6.0f}%")


if __name__ == "__main__":
    main()
