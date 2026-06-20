"""Sweep the QuantCrawler-style confluence concept across settings, IS/OOS, on
the 10-coin basket. A real edge is POSITIVE out-of-sample across a RANGE of
settings; a curve-fit one needs one magic combo. We don't know their exact
params — so we judge the concept by robustness, which their optimization can't fake.

Run: ../../venv/Scripts/python.exe conf_sweep.py [15m|1h]
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


def run(coins, tf, lo, hi, params):
    allt = []
    for c in coins:
        df = load(c, tf); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        allt += simulate(sub, MS.confluence(sub, side="both", **params), LIGHTER, RISK, TF_MIN[tf])
    return stat(allt)


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in TF_MIN else "15m"
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    print(f"confluence sweep  tf={tf}  basket={coins}  zero-fee, IS=70/OOS=30\n")
    print(f"{'min_conf':>8} {'ema':>7} {'sl':>4} {'sqz':>4} {'IS net':>8} {'IS PF':>6} "
          f"{'OOS net':>8} {'OOS PF':>7} {'OOS n':>6} {'OOS wr':>7}")
    rows = []
    for min_conf in (2, 3, 4):
        for ef, es in ((21, 50), (9, 21), (50, 200)):
            for sl in (1.0, 1.5, 2.0):
                for sqz in (False, True):
                    p = dict(ema_fast=ef, ema_slow=es, sl_atr=sl, min_conf=min_conf,
                             require_squeeze=sqz)
                    i = run(coins, tf, 0.0, 0.7, p); o = run(coins, tf, 0.7, 1.0, p)
                    rows.append((min_conf, f"{ef}/{es}", sl, sqz, i, o))
    # rank by OOS net, show top 14 + how many of ALL are OOS-positive
    rows.sort(key=lambda r: r[5]["net"], reverse=True)
    pos = sum(1 for r in rows if r[5]["net"] > 0 and r[4]["net"] > 0)
    for mc, em, sl, sqz, i, o in rows[:14]:
        opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
        ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
        print(f"{mc:>8} {em:>7} {sl:>4} {str(sqz):>4} {i['net']:>+8.0f} {ipf:>6} "
              f"{o['net']:>+8.0f} {opf:>7} {o['n']:>6} {o['wr']:>6.0f}%")
    print(f"\n{pos}/{len(rows)} settings are POSITIVE in BOTH IS and OOS.")
    print("read: many robustly-positive settings = real concept. one lucky cell "
          "with everything around it red = curve-fit, no real edge.")


if __name__ == "__main__":
    main()
