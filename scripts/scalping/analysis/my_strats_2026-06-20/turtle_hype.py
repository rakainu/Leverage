"""The Turtle (Donchian breakout) tested honestly, focused on HYPE + the basket.
Entry: close breaks the prior N-bar high (long) / low (short). Exit: ATR hard
stop + ATR trailing stop (proxy for the classic opposite-channel exit) + intraday
time cap. Sweep channel length x timeframe x stop/trail, IS vs OOS.

(breakout_hold IS the turtle entry; this drives it as a focused turtle sweep.)

Run: ../../venv/Scripts/python.exe turtle_hype.py
"""
from __future__ import annotations
import os, sys, glob
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import simulate, Costs, RiskCfg  # noqa: E402
import my_strats as MS  # noqa: E402

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
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=sum(w) + sum(l), pf=pf)


def run(coins, tf, lo, hi, p, maxbars):
    allt = []
    for c in coins:
        df = load(c, tf); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        allt += simulate(sub, MS.breakout_hold(sub, side="both", max_bars=maxbars, **p),
                         LIGHTER, RISK, TF_MIN[tf])
    return stat(allt)


def sweep(label, coins):
    print(f"\n=== {label} ===")
    print(f"{'tf':4} {'chan':>4} {'sl':>4} {'trail':>5} {'IS net':>8} {'IS PF':>6} "
          f"{'OOS net':>8} {'OOS PF':>7} {'OOS n':>6}")
    best = None
    for tf, cap in (("15m", 32), ("1h", 12)):
        for chan in (10, 20, 55):
            for sl in (1.5, 2.5):
                for trail in (2.0, 4.0):
                    p = dict(lookback=chan, sl_atr=sl, trail_mult=trail)
                    i = run(coins, tf, 0.0, 0.7, p, cap)
                    o = run(coins, tf, 0.7, 1.0, p, cap)
                    ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
                    opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
                    flag = " <" if (i["net"] > 0 and o["net"] > 0) else ""
                    print(f"{tf:4} {chan:>4} {sl:>4} {trail:>5} {i['net']:>+8.0f} {ipf:>6} "
                          f"{o['net']:>+8.0f} {opf:>7} {o['n']:>6}{flag}")


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    sweep("HYPE only", ["HYPE"])
    sweep("Whole basket (pooled)", coins)
    print("\nread: '<' marks a setting positive IS AND OOS. turtle works if those "
          "exist robustly; if all red, breakout trend-following has no edge here.")


if __name__ == "__main__":
    main()
