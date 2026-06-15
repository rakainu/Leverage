"""Focus pass on the best config from run_1h.py: breakout entry + gate ON + RUN exit.

Answers the two decisive questions:
  1. Zero-fee (Lighter) vs BloFin costs — does removing fees flip it positive?
  2. Long vs short split — is any edge real, or just longs riding a bull window
     (HYPE +126%, SOL up over the window)? A real edge survives on BOTH sides.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, ENGINE_DIR)
import btengine as bt  # noqa: E402
from run_1h import load_1h, prep, gen_signals, COINS, TF_MIN  # noqa: E402

ZERO = bt.Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.02, funding_pct_per_8h=0.01)  # Lighter-ish
BLOFIN = bt.Costs()


def run(df, entry, gate, exit_mode, costs, side_filter=None):
    risk = bt.RiskCfg(starting_equity=1000.0, risk_frac=0.01, compounding=False)
    sigs = gen_signals(df, entry, gate, exit_mode)
    if side_filter is not None:
        sigs = [s for s in sigs if s.side == side_filter]
    return bt.simulate(df, sigs, costs, risk, TF_MIN)


def line(label, trades):
    m = bt.metrics(trades, 1000.0)
    pf = m["profit_factor"]; pfs = "inf" if pf == float("inf") else f"{pf:5.2f}"
    return (f"{label:<22} n={m['n']:>4} PF={pfs} WR={m['win_rate']:4.0f}% "
            f"avgR={m['avg_r']:+.3f} net={m['net_pct']:+6.1f}% maxDD={m['max_dd_pct']:4.1f}%")


def buy_hold(df):
    return (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100


def main():
    data = {c: prep(load_1h(c)) for c in COINS}
    entry, gate, exit_mode = "breakout", True, "run"
    print("# FOCUS: breakout entry + gate ON + RUN exit (best 2x2 cell)\n")
    print("# Buy&hold over window:", {c: f"{buy_hold(data[c]):+.0f}%" for c in COINS}, "\n")

    for cname, costs in (("BloFin costs", BLOFIN), ("Zero-fee (Lighter)", ZERO)):
        print(f"\n{'='*78}\n{cname}\n{'='*78}")
        pooled_all, pooled_long, pooled_short = [], [], []
        for c in COINS:
            df = data[c]
            tr = run(df, entry, gate, exit_mode, costs)
            trl = run(df, entry, gate, exit_mode, costs, side_filter=+1)
            trs = run(df, entry, gate, exit_mode, costs, side_filter=-1)
            pooled_all += tr; pooled_long += trl; pooled_short += trs
            print("  " + line(f"{c} all", tr))
            print("      " + line("long", trl))
            print("      " + line("short", trs))
        print("  " + "-" * 74)
        print("  " + line("[POOLED] all", pooled_all))
        print("      " + line("long", pooled_long))
        print("      " + line("short", pooled_short))


if __name__ == "__main__":
    main()
