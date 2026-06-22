from __future__ import annotations
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backtest")))
from engine import Signal, rsi, sma, atr

def signals(df, lo=5, hi=95, rsi_len=2, sma_len=5, sl_atr=1.5, max_bars=24, atr_p=14, side="both"):
    C = df["Close"]
    r = rsi(C, rsi_len).values
    m = sma(C, sma_len).values
    a = atr(df, atr_p).values
    cv = C.values
    out = []
    for i in range(len(df)):
        if not np.isfinite(r[i]) or not np.isfinite(m[i]) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        sv = 0
        if r[i] < lo and cv[i] < m[i]:
            sv = 1
        elif r[i] > hi and cv[i] > m[i]:
            sv = -1
        if sv == 0 or (side == "long" and sv < 0) or (side == "short" and sv > 0):
            continue
        tp = abs(m[i] - cv[i])                 # target = revert to the mean
        if tp <= 0:
            continue
        out.append(Signal(i=i, side=sv, sl_dist=sl_atr * a[i], tp_dist=tp,
                          entry_style="market", max_bars=max_bars))
    return out
