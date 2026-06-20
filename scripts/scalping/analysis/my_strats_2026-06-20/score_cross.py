"""ODD-ONE-OUT (cross-coin divergence fade) + scoreboard.

Coins move together. Over a short window, measure each coin's return vs the
BASKET (median of all coins). When one coin breaks away from the pack (its
return diverges by z_entry standard deviations), bet it converges back: SHORT
the one that ran ahead, LONG the one that lagged. Market-achievable: maker limit
entry, ATR stop, modest target, intraday time stop.

This is genuinely different from single-coin mean-reversion — the edge is
*relative* (coin vs pack), so it should be less correlated with the scalper.

Run: ../../venv/Scripts/python.exe score_cross.py [5m|15m]
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, simulate, Costs, RiskCfg, atr, rolling_zscore  # noqa: E402

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


def odd_one_out(coin, dfs, divz, ret_lb, z_entry, atr_p=14, sl_atr=1.5,
                tp_r=1.0, limit_atr=0.10, max_bars=10):
    """Signals for one coin from its precomputed divergence z-score `divz`."""
    df = dfs[coin]
    a = atr(df, atr_p).reindex(df.index)
    z = divz.reindex(df.index)
    cv, av, zv = df["Close"].values, a.values, z.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        # ran AHEAD of the pack -> short (converge down); LAGGED -> long
        side_val = -1 if zv[i] >= z_entry else (1 if zv[i] <= -z_entry else 0)
        if side_val == 0:
            continue
        limit_off = limit_atr * av[i]
        sl_dist = sl_atr * av[i]
        tp_dist = tp_r * sl_dist
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp_dist,
                           entry_style="limit", limit_dist=limit_off, max_bars=max_bars))
    return sigs


def build_divergence(dfs, ret_lb, z_period):
    """Per-coin divergence z-score = z( coin_cumret - basket_median_cumret )."""
    closes = pd.DataFrame({c: dfs[c]["Close"] for c in dfs}).dropna()
    rets = np.log(closes).diff()
    cumret = rets.rolling(ret_lb).sum()
    basket = cumret.median(axis=1)
    out = {}
    for c in dfs:
        diverg = cumret[c] - basket
        out[c] = rolling_zscore(diverg, z_period)
    return out


def pooled(coins, dfs, tf, lo, hi, ret_lb, z_entry, z_period):
    sub = {c: dfs[c].iloc[int(len(dfs[c]) * lo):int(len(dfs[c]) * hi)] for c in coins}
    divz = build_divergence(sub, ret_lb, z_period)
    n = wins = liq = 0; wsum = lsum = 0.0; curve = []
    for c in coins:
        sigs = odd_one_out(c, sub, divz[c], ret_lb, z_entry)
        trades = simulate(sub[c], sigs, LIGHTER, RISK, TF_MIN[tf])
        for t in trades:
            n += 1
            if t.pnl_usd > 0:
                wins += 1; wsum += t.pnl_usd
            else:
                lsum += t.pnl_usd
            curve.append(t.pnl_usd)
            if t.exit_reason == "liquidation":
                liq += 1
    pf = wsum / -lsum if lsum < 0 else float("inf")
    net = wsum + lsum
    cum = np.cumsum(curve) if curve else np.array([0.0])
    dd = float((cum - np.maximum.accumulate(cum)).min()) if len(cum) else 0.0
    return dict(n=n, wr=wins / n * 100 if n else 0, net=net, pf=pf, dd=dd,
                pert=net / n if n else 0, liq=liq)


def main():
    tf = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in TF_MIN else "5m"
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    if len(coins) < 3:
        print("need >=3 coins for cross-coin; wait for fetch"); return
    dfs = {c: load(c, tf) for c in coins}
    print(f"odd_one_out  basket={coins}  tf={tf}  zero-fee Lighter\n")
    print(f"{'ret_lb':>6} {'z':>4} {'split':4} {'trades':>7} {'win%':>5} {'net$':>8} "
          f"{'PF':>6} {'worstDD$':>9} {'$/trade':>8} {'liq':>4}")
    for ret_lb in (3, 6):
        for z_entry in (2.0, 2.5):
            for lbl, lo, hi in (("IS", 0.0, 0.7), ("OOS", 0.7, 1.0)):
                m = pooled(coins, dfs, tf, lo, hi, ret_lb, z_entry, z_period=50)
                pf = "inf" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
                print(f"{ret_lb:>6} {z_entry:>4} {lbl:4} {m['n']:>7} {m['wr']:>5.0f} "
                      f"{m['net']:>+8.0f} {pf:>6} {m['dd']:>9.0f} {m['pert']:>+8.2f} {m['liq']:>4}")
            print()


if __name__ == "__main__":
    main()
