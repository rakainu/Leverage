"""Flawless Victory concept (Bollinger mean-reversion) + the decisive test of the
refined pattern: does TREND-GATING a quick-revert flip it positive?

Entry: close pierces the lower BB(20,2) [long] / upper [short], with RSI not at
the opposite extreme. Target = the BB basis (the mean) — a small quick revert.
ATR stop. Tested 4 ways: both-sides ungated, and trend-gated (only buy dips in
an uptrend / short rips in a downtrend, EMA200) — to isolate whether the gate is
the edge. 5m + 15m, IS/OOS, zero-fee.

Run: ../../venv/Scripts/python.exe flawless.py
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


def signals(df, bb_len=20, bb_mult=2.0, rsi_len=14, sl_atr=2.0, max_bars=12,
            trend_gate=False, tp_frac=0.7):
    C = df["Close"]
    basis = sma(C, bb_len); dev = bb_mult * C.rolling(bb_len, min_periods=bb_len).std(ddof=0)
    ub, lb = basis + dev, basis - dev
    r = rsi(C, rsi_len).values
    a = atr(df, 14).values
    e200 = ema(C, 200).values
    cv, bv, uv, lv = C.values, basis.values, ub.values, lb.values
    sigs = []
    for i in range(1, len(df)):
        if any(np.isnan(x) for x in (bv[i], uv[i], lv[i], r[i], a[i])) or a[i] <= 0:
            continue
        long_sig = cv[i] <= lv[i] and r[i] > 35
        short_sig = cv[i] >= uv[i] and r[i] < 65
        side = 1 if long_sig else (-1 if short_sig else 0)
        if side == 0:
            continue
        if trend_gate:
            if np.isnan(e200[i]):
                continue
            # only fade WITH the trend: buy dips above EMA200, short rips below
            if side == 1 and cv[i] < e200[i]:
                continue
            if side == -1 and cv[i] > e200[i]:
                continue
        tp = abs(bv[i] - cv[i]) * tp_frac          # quick revert toward the mean
        sl = sl_atr * a[i]
        if tp <= 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl, tp_dist=tp,
                           entry_style="market", max_bars=max_bars))
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
    print(f"Flawless Victory (BB mean-reversion), basket, zero-fee\n")
    print(f"{'tf':4} {'gate':>5} {'IS net':>8} {'IS PF':>6} {'IS n':>5} "
          f"{'OOS net':>8} {'OOS PF':>7} {'OOS n':>5} {'OOS wr':>7}")
    for tf in ("5m", "15m"):
        for gate in (False, True):
            i = run(coins, tf, 0.0, 0.7, trend_gate=gate)
            o = run(coins, tf, 0.7, 1.0, trend_gate=gate)
            ipf = "inf" if i["pf"] == float("inf") else f"{i['pf']:.2f}"
            opf = "inf" if o["pf"] == float("inf") else f"{o['pf']:.2f}"
            flag = " <" if (i["net"] > 0 and o["net"] > 0) else ""
            print(f"{tf:4} {str(gate):>5} {i['net']:>+8.0f} {ipf:>6} {i['n']:>5} "
                  f"{o['net']:>+8.0f} {opf:>7} {o['n']:>5} {o['wr']:>6.0f}%{flag}")
    print("\nread: if trend-gated flips positive where ungated is red, the GATE "
          "(fade WITH the trend) is the edge — exactly what the scalper does.")


if __name__ == "__main__":
    main()
