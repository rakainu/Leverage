"""Does a trend-clarity floor (min_slope_pct) fix the losing window WITHOUT
hurting the good ones? Re-run all 9 chunks at several floors. A CLEAN fix lifts
the bad chunk (1) toward positive while keeping total net + the good chunks
roughly intact or better. If it cuts the winners, it's conservatism -> reject.

Run: ../../venv/Scripts/python.exe slope_gate_test.py
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
from btengine import simulate, Costs, RiskCfg  # noqa: E402
import strat_lib as SL  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10,
               liq_buffer=2.5, compounding=True)
BASE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14, accel_mult=3.0)
N = 9


def load(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def chunk_nets(dfs, coins, bounds, floor):
    p = dict(BASE, min_slope_pct=floor)
    nets, ns = [], []
    for k in range(N):
        a, b = bounds[k], bounds[k + 1]
        net = 0.0; n = 0
        for c in coins:
            sub = dfs[c].iloc[a:b]
            tr = simulate(sub, SL.regime_mr(sub, side="both", **p), LIGHTER, RISK, 15)
            net += sum(t.pnl_usd for t in tr); n += len(tr)
        nets.append(net); ns.append(n)
    return nets, ns


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    dfs = {c: load(c) for c in coins}
    L = min(len(dfs[c]) for c in coins)
    bounds = [int(L * k / N) for k in range(N + 1)]
    print("net$ per chunk at each trend-clarity floor (chunk 1 = the loser)\n")
    print(f"{'floor':>6} | " + " ".join(f"c{k}".rjust(6) for k in range(N)) +
          f" | {'TOTAL':>7} {'trades':>7} {'c1':>6} {'goods':>7}")
    for floor in (0.0, 0.03, 0.05, 0.08, 0.12):
        nets, ns = chunk_nets(dfs, coins, bounds, floor)
        total = sum(nets); tn = sum(ns)
        goods = sum(nets[k] for k in (0, 2, 4, 5, 6, 7, 8))   # the 7 winners at baseline
        cells = " ".join(f"{v:+6.0f}" for v in nets)
        print(f"{floor:>6} | {cells} | {total:>+7.0f} {tn:>7} {nets[1]:>+6.0f} {goods:>+7.0f}")
    print("\nread: clean fix = c1 rises toward 0+ AND TOTAL and 'goods' stay ~same "
          "or better. if TOTAL/goods drop a lot, the floor is cutting good trades "
          "(conservatism) — reject it.")


if __name__ == "__main__":
    main()
