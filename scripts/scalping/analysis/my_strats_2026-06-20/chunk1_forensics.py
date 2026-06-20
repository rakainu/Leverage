"""Forensics on the losing window (chunk 1, ~04-01->04-11): WHY did the scalper
bleed -$926? Find a FACTUAL, SEPARATING cause — a measurable feature where the
losers cluster and the winners don't — or conclude it's just variance (change
nothing). NO blanket conservatism: a fix is only justified if it removes the
bad trades without touching the good ones.

Records each trade's entry conditions (coin, side, z-score, EMA200 slope, ADX,
hour, bar range/ATR) and compares losers vs winners.

Run: ../../venv/Scripts/python.exe chunk1_forensics.py [chunk_index]
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
from btengine import simulate, Costs, RiskCfg, atr, ema, adx, rolling_zscore  # noqa: E402
import strat_lib as SL  # noqa: E402
from strat_lib import session_vwap  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=3600.0, risk_frac=0.01, max_leverage=10,
               liq_buffer=2.5, compounding=True)
LIVE = dict(trend_len=200, slope_lb=20, z_period=30, z_entry=1.5, sl_atr=2.0,
            tp_frac=0.3, max_bars=12, limit_atr=0.25, atr_p=14, accel_mult=3.0)
N = 9
K = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1


def load(coin):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def feats(df):
    C = df["Close"]
    e = ema(C, 200); slope = (e - e.shift(20)) / e * 100
    vwap = session_vwap(df); z = rolling_zscore(C - vwap, 30)
    a = atr(df, 14); adxv = adx(df, 14)
    rng_atr = (df["High"] - df["Low"]) / a
    return dict(slope=slope.values, z=z.values, adx=adxv.values, rng=rng_atr.values,
                hour=df.index.hour.values)


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    dfs = {c: load(c) for c in coins}
    L = min(len(dfs[c]) for c in coins)
    a, b = int(L * K / N), int(L * (K + 1) / N)
    rows = []
    by_coin = {}
    for c in coins:
        sub = dfs[c].iloc[a:b]
        f = feats(sub)
        trs = simulate(sub, SL.regime_mr(sub, side="both", **LIVE), LIGHTER, RISK, 15)
        cnet = 0.0
        for t in trs:
            ei = t.entry_i
            rows.append(dict(coin=c, side="L" if t.side > 0 else "S", pnl=t.pnl_usd,
                             reason=t.exit_reason, hour=int(f["hour"][ei]),
                             z=f["z"][ei], slope=f["slope"][ei], adx=f["adx"][ei],
                             rng=f["rng"][ei], time=sub.index[ei]))
            cnet += t.pnl_usd
        by_coin[c] = cnet
    d = pd.DataFrame(rows)
    print(f"chunk {K}: {dfs[coins[0]].iloc[a:b].index[0]:%m-%d}->"
          f"{dfs[coins[0]].iloc[a:b].index[-1]:%m-%d}  {len(d)} trades  net ${d.pnl.sum():+.0f}\n")

    print("=== net by coin ===")
    for c, v in sorted(by_coin.items(), key=lambda x: x[1]):
        print(f"  {c:5} {v:+8.0f}")
    print("\n=== net by side ===")
    for s in ("L", "S"):
        sd = d[d.side == s]
        print(f"  {s}: n={len(sd)} net={sd.pnl.sum():+.0f} WR={ (sd.pnl>0).mean()*100:.0f}%")

    print("\n=== 12 worst trades ===")
    print(f"{'coin':5}{'side':>4}{'pnl':>8}{'reason':>10}{'hr':>4}{'z':>7}{'slope%':>8}{'adx':>6}{'rng/atr':>8}  time")
    for _, r in d.nsmallest(12, "pnl").iterrows():
        print(f"{r.coin:5}{r.side:>4}{r.pnl:>8.0f}{r.reason:>10}{r.hour:>4}{r.z:>7.2f}"
              f"{r.slope:>8.3f}{r.adx:>6.0f}{r['rng']:>8.2f}  {r.time:%m-%d %H:%M}")

    print("\n=== losers vs winners: feature means (look for a SEPARATING feature) ===")
    win, los = d[d.pnl > 0], d[d.pnl <= 0]
    print(f"{'feature':10}{'winners':>10}{'losers':>10}")
    for col in ("z", "slope", "adx", "rng", "hour"):
        print(f"{col:10}{win[col].mean():>10.3f}{los[col].mean():>10.3f}")
    print(f"{'|z|':10}{win.z.abs().mean():>10.3f}{los.z.abs().mean():>10.3f}")
    print("\nread: if a feature cleanly separates losers from winners, a TARGETED "
          "filter on it removes the bad trades without touching the good. if "
          "nothing separates, it's variance — change NOTHING.")


if __name__ == "__main__":
    main()
