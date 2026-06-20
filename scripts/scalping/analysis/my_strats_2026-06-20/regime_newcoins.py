"""Validate the ACTUAL live scalper (strat_lib.regime_mr, live params + accel
guard 3.0) on candidate NEW coins vs the existing live basket as a benchmark.
Only coins that clear the bar IN and OUT of sample get added live.

Run: ../../venv/Scripts/python.exe regime_newcoins.py
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
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=10,
               liq_buffer=2.5, compounding=True)
# EXACT live config (config.scalper.yaml) incl. the deployed accel guard.
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14, accel_mult=3.0)

EXISTING = ["ETH", "BTC", "SOL", "HYPE", "BNB"]   # live basket (XMR not on OKX)
CANDIDATES = ["AVAX", "DOGE", "LINK", "SUI"]      # new (XRP excluded: already cut)


def load(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=sum(w) + sum(l), pf=pf)


def run(coin, lo, hi):
    df = load(coin); a, b = int(len(df) * lo), int(len(df) * hi)
    sub = df.iloc[a:b]
    return stat(simulate(sub, SL.regime_mr(sub, side="both", **LIVE), LIGHTER, RISK, 15))


def line(coin, tag):
    i = run(coin, 0.0, 0.7); o = run(coin, 0.7, 1.0)
    ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
    opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
    verdict = "ADD" if (o["net"] > 0 and o["pf"] >= 1.15 and o["wr"] >= 80 and o["n"] >= 20
                        and i["net"] > 0) else "no"
    print(f"{tag:5} {coin:5} | IS net {i['net']:>+6.0f} PF {ipf:>5} | "
          f"OOS net {o['net']:>+6.0f} PF {opf:>5} WR {o['wr']:>3.0f}% n {o['n']:>3} | {verdict}")
    return verdict == "ADD"


def main():
    have = {os.path.basename(p).split("_")[0] for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet"))}
    print("regime_mr (LIVE params + accel 3.0), 15m, zero-fee, 10x. 90d, IS=first70% OOS=last30%\n")
    print("--- EXISTING live basket (benchmark for 'what passing looks like') ---")
    for c in EXISTING:
        if c in have:
            line(c, "live")
    print("\n--- CANDIDATE new coins ---")
    adds = []
    for c in CANDIDATES:
        if c in have and line(c, "cand"):
            adds.append(c)
    print(f"\nADD verdict (OOS net>0, PF>=1.15, WR>=80%, n>=20, IS net>0): "
          f"{adds or 'none clear the bar'}")
    print("note: still need Lighter market_id for each before live. XMR (live "
          "winner) untestable here (not on OKX) — unaffected.")


if __name__ == "__main__":
    main()
