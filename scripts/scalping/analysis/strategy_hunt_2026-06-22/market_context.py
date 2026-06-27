"""Was the test window directionally tradeable, or chop? Context for the universal
directional failure. Per coin (1h): buy&hold return, time above EMA200 (trend
persistence), price max drawdown, and Kaufman Efficiency Ratio (trend vs chop).
"""
from __future__ import annotations
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import ema  # noqa: E402
sys.path.insert(0, HERE)
from stage2b_basket import load_tf, COINS  # noqa: E402


def er(close, n=100):
    net = close.diff(n).abs()
    vol = close.diff().abs().rolling(n).sum()
    return (net / vol).mean()


print("Market character over the test window (1h bars):")
print(f"  {'coin':<6}{'days':>5}{'buy&hold%':>11}{'%>EMA200':>10}{'priceMaxDD%':>13}{'meanER(100)':>12}")
bh_all = []
for c in COINS:
    d = load_tf(c, "1h")
    days = (d.index[-1] - d.index[0]).days
    bh = (d["Close"].iloc[-1] / d["Close"].iloc[0] - 1) * 100
    bh_all.append(bh)
    e200 = ema(d["Close"], 200)
    above = (d["Close"] > e200).mean() * 100
    peak = d["Close"].cummax()
    mdd = ((peak - d["Close"]) / peak).max() * 100
    e = er(d["Close"], 100)
    print(f"  {c:<6}{days:>5}{bh:>+11.0f}{above:>9.0f}%{mdd:>12.0f}%{e:>12.3f}")
print(f"\n  basket mean buy&hold: {np.mean(bh_all):+.0f}%   median: {np.median(bh_all):+.0f}%")
print("  (ER ~>0.3 = trending; ~<0.2 = choppy. %>EMA200 near 50 = no persistent trend.)")
