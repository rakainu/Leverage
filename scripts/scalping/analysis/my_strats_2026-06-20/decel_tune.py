"""Dig into the one lead: decel_fade on 15m. (1) per-coin IS/OOS so we see who
carries it; (2) does the deceleration filter actually help (on vs off);
(3) a small param grid (z_entry, tp_frac, sl_atr) ranked by OOS net.

Run: ../../venv/Scripts/python.exe decel_tune.py
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, Costs, RiskCfg  # noqa: E402
import my_strats as MS  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
TF = "15m"


def load(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    net = sum(w) + sum(l)
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=net, pf=pf)


def run(coins, lo, hi, params):
    allt = []
    for c in coins:
        df = load(c); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        allt += simulate(sub, MS.decel_fade(sub, side="both", **params), LIGHTER, RISK, 15)
    return stat(allt)


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    base = dict(z_period=30, z_entry=1.8, sl_atr=1.5, tp_frac=0.5, limit_atr=0.15, max_bars=10)
    print(f"decel_fade 15m  basket={coins}\n")

    print("=== (1) PER-COIN (base params) ===")
    print(f"{'coin':5} {'IS net$':>8} {'IS PF':>6} {'OOS net$':>9} {'OOS PF':>7} {'OOS n':>6} {'OOS wr':>7}")
    for c in coins:
        i = run([c], 0.0, 0.7, base); o = run([c], 0.7, 1.0, base)
        ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
        opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
        print(f"{c:5} {i['net']:>+8.0f} {ipf:>6} {o['net']:>+9.0f} {opf:>7} {o['n']:>6} {o['wr']:>6.0f}%")

    print("\n=== (2) DECEL FILTER on vs off (pooled) ===")
    for req in (True, False):
        p = dict(base, require_decel=req)
        i = run(coins, 0.0, 0.7, p); o = run(coins, 0.7, 1.0, p)
        print(f"  require_decel={str(req):5}  IS net {i['net']:+.0f} PF {i['pf']:.2f} | "
              f"OOS net {o['net']:+.0f} PF {o['pf']:.2f} n {o['n']}")

    print("\n=== (3) PARAM GRID (pooled), ranked by OOS net ===")
    rows = []
    for z in (1.5, 1.8, 2.2, 2.6):
        for tp in (0.4, 0.6, 0.8):
            for sl in (1.0, 1.5, 2.0):
                p = dict(base, z_entry=z, tp_frac=tp, sl_atr=sl)
                i = run(coins, 0.0, 0.7, p); o = run(coins, 0.7, 1.0, p)
                rows.append((z, tp, sl, i["net"], i["pf"], o["net"], o["pf"], o["n"]))
    rows.sort(key=lambda r: r[5], reverse=True)
    print(f"{'z':>4} {'tp':>4} {'sl':>4} {'IS net':>8} {'IS PF':>6} {'OOS net':>8} {'OOS PF':>7} {'OOS n':>6}")
    for z, tp, sl, inet, ipf, onet, opf, on in rows[:12]:
        print(f"{z:>4} {tp:>4} {sl:>4} {inet:>+8.0f} {ipf:>6.2f} {onet:>+8.0f} {opf:>7.2f} {on:>6}")
    print("\nread: a real edge is POSITIVE in BOTH IS and OOS and survives across "
          "several nearby params + multiple coins. one lucky cell = noise.")


if __name__ == "__main__":
    main()
