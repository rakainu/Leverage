"""Retest the ONE momentum idea that previously validated: squeeze_expansion
(breakout only after a volatility SQUEEZE / compression). My naive breakout_hold
failed because it fires on any breakout; this only fires from a coiled spring.

Tests strat_lib.squeeze_expansion on the current 10-coin data, 15m + 1h, IS/OOS,
with max_bars CAPPED so holds stay intraday (Rich's constraint). Also tries a
squeeze pre-filter on my own breakout_hold.

Run: ../../venv/Scripts/python.exe momentum_v2.py
"""
from __future__ import annotations
import os, sys, glob
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
from btengine import simulate, Costs, RiskCfg  # noqa: E402
import strat_lib as SL  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
TF_MIN = {"15m": 15, "1h": 60}


def load(coin, tf):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    rule = {"15m": "15min", "1h": "1h"}[tf]
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min",
                                  "Close": "last", "Volume": "sum"}).dropna()


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100 if trades else 0,
                net=sum(w) + sum(l), pf=pf)


def run(coins, tf, lo, hi, params, maxbars):
    allt = []
    for c in coins:
        df = load(c, tf); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        p = dict(params, max_bars=maxbars)
        allt += simulate(sub, SL.REGISTRY["squeeze_expansion"](sub, side="both", **p), LIGHTER, RISK, TF_MIN[tf])
    return stat(allt)


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    base = dict(bb_len=20, sl_atr=1.5, tp_atr=3.0, min_squeeze=6)
    print(f"squeeze_expansion (intraday-capped), basket={coins}, zero-fee\n")
    print(f"{'tf':4} {'maxbars(hold)':>13} {'split':4} {'trades':>7} {'win%':>5} "
          f"{'net$':>8} {'PF':>6} {'$/trade':>8}")
    for tf, caps in (("15m", [16, 32]), ("1h", [8, 12])):
        for mb in caps:
            hold = mb * TF_MIN[tf] / 60.0
            for lbl, lo, hi in (("IS", 0.0, 0.7), ("OOS", 0.7, 1.0)):
                m = run(coins, tf, lo, hi, base, mb)
                pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
                print(f"{tf:4} {f'{mb}b ({hold:.0f}h)':>13} {lbl:4} {m['n']:>7} "
                      f"{m['wr']:>5.0f} {m['net']:>+8.0f} {pf:>6} {m['pert']  if False else m['net']/m['n'] if m['n'] else 0:>+8.2f}")
            print()
    print("read: squeeze breakout wins if net POSITIVE IS+OOS at PF>1.2 with "
          "intraday holds. if it only worked with 2-day holds, it's off-limits.")


if __name__ == "__main__":
    main()
