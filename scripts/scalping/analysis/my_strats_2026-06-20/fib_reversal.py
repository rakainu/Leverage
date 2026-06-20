"""Port of Rich's "Fibonacci Trend Reversals" slot (actual title: EMA Reverse
Scalper). A COUNTER-TREND FADE:
  - EMAs 10/20/50/100/200; require a full stack (strong trend).
  - Bollinger(10, 2): price closed OUTSIDE the band last bar, back INSIDE now.
  - Stoch-RSI(9,9,3,3): both K,D at an extreme (>92 short / <8 long).
  - SHORT when uptrend-stacked + overbought re-entry; LONG when downtrend + oversold.
  - Original exit: limit at EMA200 (no stop). We ADD a stop + intraday time cap
    (the original could hold for days -> breaks the intraday rule) and also cap
    the take-profit so it's reachable. tp = min(dist-to-ema200, tp_cap_atr*ATR).

Tested on the basket, 5m + 15m, IS/OOS, zero-fee. Setup is very selective so
trade counts will be low.

Run: ../../venv/Scripts/python.exe fib_reversal.py
"""
from __future__ import annotations
import os, sys, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, simulate, Costs, RiskCfg, atr, ema, sma, rsi  # noqa: E402

LIGHTER = Costs(taker_pct=0.0, maker_pct=0.0, slippage_pct=0.05, funding_pct_per_8h=0.01)
RISK = RiskCfg(starting_equity=1000.0, risk_frac=0.01, max_leverage=20,
               liq_buffer=2.5, compounding=True)
TF_MIN = {"5m": 5, "15m": 15}


def load(coin, tf):
    df = pd.read_parquet(os.path.join(HERE, "data", f"{coin}_5m.parquet")).astype(float)
    if tf == "5m":
        return df
    return df.resample("15min").agg({"Open": "first", "High": "max", "Low": "min",
                                     "Close": "last", "Volume": "sum"}).dropna()


def stoch_rsi(close, rsi_len=9, stoch_len=9, k=3, d=3):
    r = rsi(close, rsi_len)
    lo = r.rolling(stoch_len, min_periods=stoch_len).min()
    hi = r.rolling(stoch_len, min_periods=stoch_len).max()
    st = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    kk = st.rolling(k, min_periods=k).mean()
    dd = kk.rolling(d, min_periods=d).mean()
    return kk, dd


def signals(df, sl_atr=2.0, tp_cap_atr=6.0, max_bars=48, ul=92, ll=8, require_stack=True):
    C = df["Close"]
    e10, e20, e50, e100, e200 = (ema(C, n) for n in (10, 20, 50, 100, 200))
    basis = sma(C, 10); dev = 2.0 * C.rolling(10, min_periods=10).std(ddof=0)
    upper, lower = basis + dev, basis - dev
    kk, dd = stoch_rsi(C)
    a = atr(df, 14)
    cv = C.values; uv, lv = upper.values, lower.values
    kv, dv = kk.values, dd.values
    e200v, e100v, e50v, e20v, e10v = e200.values, e100.values, e50.values, e20.values, e10.values
    av = a.values
    sigs = []
    for i in range(1, len(df)):
        if any(np.isnan(x) for x in (uv[i], lv[i], kv[i], dv[i], e200v[i], av[i])) or av[i] <= 0:
            continue
        up_stack = e10v[i] > e20v[i] > e50v[i] > e100v[i] > e200v[i]
        dn_stack = e10v[i] < e20v[i] < e50v[i] < e100v[i] < e200v[i]
        if require_stack and not (up_stack or dn_stack):
            continue
        bear = (cv[i - 1] > uv[i - 1]) and (cv[i] < uv[i]) and kv[i - 1] > ul and dv[i - 1] > ul
        bull = (cv[i - 1] < lv[i - 1]) and (cv[i] > lv[i]) and kv[i - 1] < ll and dv[i - 1] < ll
        side = 0
        if cv[i] > e200v[i] and bear and up_stack:
            side = -1
        elif cv[i] < e200v[i] and bull and dn_stack:
            side = 1
        if side == 0:
            continue
        dist = abs(cv[i] - e200v[i])
        tp = min(dist, tp_cap_atr * av[i])
        sl = sl_atr * av[i]
        if tp <= 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl, tp_dist=tp, entry_style="market",
                           max_bars=max_bars))
    return sigs


def stat(trades):
    if not trades:
        return dict(n=0, wr=0, net=0.0, pf=0.0)
    w = [t.pnl_usd for t in trades if t.pnl_usd > 0]
    l = [t.pnl_usd for t in trades if t.pnl_usd <= 0]
    pf = sum(w) / -sum(l) if sum(l) < 0 else float("inf")
    return dict(n=len(trades), wr=len(w) / len(trades) * 100, net=sum(w) + sum(l), pf=pf)


def run(coins, tf, lo, hi, **kw):
    allt = []
    for c in coins:
        df = load(c, tf); a, b = int(len(df) * lo), int(len(df) * hi)
        sub = df.iloc[a:b]
        allt += simulate(sub, signals(sub, **kw), LIGHTER, RISK, TF_MIN[tf])
    return stat(allt)


def main():
    coins = sorted(os.path.basename(p).split("_")[0]
                   for p in glob.glob(os.path.join(HERE, "data", "*_5m.parquet")))
    print(f"EMA Reverse Scalper (fade extremes in a strong trend), basket={coins}, zero-fee\n")
    print(f"{'tf':4} {'sl_atr':>6} {'maxbars':>7} {'IS net':>8} {'IS PF':>6} {'IS n':>5} "
          f"{'OOS net':>8} {'OOS PF':>7} {'OOS n':>5} {'OOS wr':>7}")
    for tf in ("5m", "15m"):
        for sl in (1.5, 2.5):
            for mb in (24, 48):
                i = run(coins, tf, 0.0, 0.7, sl_atr=sl, max_bars=mb)
                o = run(coins, tf, 0.7, 1.0, sl_atr=sl, max_bars=mb)
                ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
                opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
                flag = " <" if (i["net"] > 0 and o["net"] > 0) else ""
                print(f"{tf:4} {sl:>6} {mb:>7} {i['net']:>+8.0f} {ipf:>6} {i['n']:>5} "
                      f"{o['net']:>+8.0f} {opf:>7} {o['n']:>5} {o['wr']:>6.0f}%{flag}")
    print("\nread: '<' = positive IS AND OOS. it's a fade (the family that works) "
          "but very selective — watch the trade count.")


if __name__ == "__main__":
    main()
