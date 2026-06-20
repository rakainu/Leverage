"""Plain scoreboard for my own strategies — honest engine, zero-fee Lighter,
in-sample (first 70%) vs out-of-sample (last 30%) so overfit shows itself.

Reports per strategy, pooled across the basket:
  trades | win% | net$ (on $1000 start) | profit-factor | worst drawdown$ | $/trade
both for IS and OOS. A strategy is only interesting if it makes money OOS too.

Run: ../../venv/Scripts/python.exe score.py [5m|15m] [days_back_cap]
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, metrics, Costs, RiskCfg  # noqa: E402
import my_strats as MS  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
TF_MIN = {"5m": 5, "15m": 15, "1h": 60}


def load(coin, tf):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    if tf == "5m":
        return df
    rule = {"15m": "15min", "1h": "1h"}[tf]
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()


def pooled(strat, coins, tf, lo, hi):
    """Run one strategy across coins over the [lo,hi) fraction of each series."""
    n = net = wins = liq = 0
    wsum = lsum = 0.0
    eq_curve = []
    for c in coins:
        df = load(c, tf)
        a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        sigs = MS.REGISTRY[strat](sub, side="both")
        trades = simulate(sub, sigs, LIGHTER, RISK, TF_MIN[tf])
        for t in trades:
            n += 1
            if t.pnl_usd > 0:
                wins += 1; wsum += t.pnl_usd
            else:
                lsum += t.pnl_usd
            eq_curve.append(t.pnl_usd)
        liq += sum(1 for t in trades if t.eff_leverage > 0 and t.exit_reason == "liquidation")
    pf = wsum / -lsum if lsum < 0 else float("inf")
    net = wsum + lsum
    wr = wins / n * 100 if n else 0
    # pooled drawdown on the concatenated per-trade pnl (rough, cross-coin)
    cum = np.cumsum(eq_curve) if eq_curve else np.array([0.0])
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    return dict(n=n, wr=wr, net=net, pf=pf, dd=dd, pert=net / n if n else 0.0, liq=liq)


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in TF_MIN else "5m"
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    if not coins:
        print("no data yet — run fetch_my.py"); return
    print(f"basket={coins}  tf={tf}  zero-fee Lighter, 1% risk, 20x cap, $1000 start\n")
    print(f"{'strategy':12} {'split':4} {'trades':>7} {'win%':>5} {'net$':>8} "
          f"{'PF':>6} {'worstDD$':>9} {'$/trade':>8} {'liq':>4}")
    for strat in MS.REGISTRY:
        for lbl, lo, hi in (("IS", 0.0, 0.7), ("OOS", 0.7, 1.0)):
            m = pooled(strat, coins, tf, lo, hi)
            pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
            print(f"{strat:12} {lbl:4} {m['n']:>7} {m['wr']:>5.0f} {m['net']:>+8.0f} "
                  f"{pf:>6} {m['dd']:>9.0f} {m['pert']:>+8.2f} {m['liq']:>4}")
        print()
    print("read: want net$ POSITIVE in BOTH IS and OOS, PF>1.2, enough trades, "
          "liq=0. OOS positive = the edge isn't just curve-fit to the past.")


if __name__ == "__main__":
    main()
