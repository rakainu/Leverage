from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from engine import ema, adx

def classify(df, adx_trend=25.0, adx_range=20.0, ema_len=100, slope_lb=3, confirm_bars=2):
    c = df["Close"]
    a = adx(df, 14).shift(1)                 # confirmed prior bar -> causal
    e = ema(c, ema_len)
    rising = (e > e.shift(slope_lb)).shift(1)
    falling = (e < e.shift(slope_lb)).shift(1)
    av, rv, fv = a.values, rising.values, falling.values
    raw = np.zeros(len(df), dtype=float)     # raw per-bar state
    for i in range(len(df)):
        if not np.isfinite(av[i]):
            raw[i] = 0; continue
        if av[i] >= adx_trend and rv[i] == True:   # noqa: E712
            raw[i] = 1
        elif av[i] >= adx_trend and fv[i] == True:  # noqa: E712
            raw[i] = -1
        elif av[i] <= adx_range:
            raw[i] = 0
        else:
            raw[i] = raw[i-1] if i else 0     # dead-band: hold previous
    # hysteresis: only switch after confirm_bars consecutive agreeing raw states
    out = np.zeros(len(df), dtype=int)
    state = 0; run_val = raw[0]; run_len = 0
    for i in range(len(df)):
        if raw[i] == run_val:
            run_len += 1
        else:
            run_val = raw[i]; run_len = 1
        if run_len >= confirm_bars and run_val != state:
            state = int(run_val)
        out[i] = state
    return pd.Series(out, index=df.index)
