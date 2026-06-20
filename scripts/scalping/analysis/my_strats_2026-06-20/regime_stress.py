"""Robustness stress test: how does the LIVE scalper (regime_mr + accel guard)
hold up as the market changes? Split 90d into sequential chunks, label each by
market character (trendiness = efficiency ratio; realized vol), and show the
scalper's PF/net/WR per chunk. Answers "what happens when the market shifts."

Run: ../../venv/Scripts/python.exe regime_stress.py [n_chunks]
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
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14, accel_mult=3.0)
N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 9


def load(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def efficiency(closes):
    """Trendiness: |net move| / total path length. ~1 = pure trend, ~0 = chop."""
    diff = np.abs(np.diff(closes))
    denom = diff.sum()
    return abs(closes[-1] - closes[0]) / denom if denom > 0 else 0.0


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    dfs = {c: load(c) for c in coins}
    L = min(len(dfs[c]) for c in coins)
    bounds = [int(L * k / N) for k in range(N + 1)]
    print(f"LIVE scalper (regime_mr + accel 3.0) across {N} time-chunks, 15m, "
          f"basket={coins}, zero-fee\n")
    print(f"{'chunk':>5} {'dates':23} {'trend%':>6} {'vol%':>5} {'trades':>6} "
          f"{'WR':>4} {'net$':>7} {'PF':>5}")
    for k in range(N):
        a, b = bounds[k], bounds[k + 1]
        net = 0.0; wins = nt = 0; wsum = lsum = 0.0
        effs = []; vols = []
        d0 = d1 = None
        for c in coins:
            sub = dfs[c].iloc[a:b]
            if d0 is None:
                d0, d1 = sub.index[0], sub.index[-1]
            cl = sub["Close"].values
            effs.append(efficiency(cl))
            vols.append(np.std(np.diff(np.log(cl))) * 100)
            tr = simulate(sub, SL.regime_mr(sub, side="both", **LIVE), LIGHTER, RISK, 15)
            for t in tr:
                nt += 1
                if t.pnl_usd > 0:
                    wins += 1; wsum += t.pnl_usd
                else:
                    lsum += t.pnl_usd
        pf = wsum / -lsum if lsum < 0 else float("inf")
        net = wsum + lsum
        wr = wins / nt * 100 if nt else 0
        pfs = "inf" if pf == float("inf") else f"{pf:.2f}"
        dates = f"{d0:%m-%d}->{d1:%m-%d}"
        print(f"{k:>5} {dates:23} {np.mean(effs)*100:>5.0f}% {np.mean(vols):>4.1f}% "
              f"{nt:>6} {wr:>3.0f}% {net:>+7.0f} {pfs:>5}")
    print("\nread: trend% = how trending the market was that chunk (high=trending, "
          "low=choppy). Watch whether the scalper's PF/net collapses in the "
          "high-trend chunks — that's its failure mode when the market changes.")


if __name__ == "__main__":
    main()
