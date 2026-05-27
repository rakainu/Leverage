"""Pine Script-equivalent indicators. Mirrors sweeps/2026-05-20/engine.py.

Keep in sync with the sweep workspace — any change here MUST be reflected
in the offline engine to maintain backtest/live parity.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.ema(src, length)`. Standard EMA with SMA seed at index period-1."""
    out = series.ewm(span=period, adjust=False, min_periods=period).mean()
    if len(series) >= period:
        seed = series.iloc[:period].mean()
        alpha = 2.0 / (period + 1)
        vals = out.values.copy()
        s_vals = series.values
        vals[period - 1] = seed
        for i in range(period, len(vals)):
            vals[i] = alpha * s_vals[i] + (1 - alpha) * vals[i - 1]
        out = pd.Series(vals, index=series.index)
    return out


def calc_smma(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.rma(src, length)` / Wilder SMMA. Seed = SMA of first `period` values."""
    vals = series.values.astype(float)
    out = np.full_like(vals, np.nan, dtype=float)
    if len(vals) < period:
        return pd.Series(out, index=series.index)
    seed_window = vals[:period]
    seed_window = seed_window[~np.isnan(seed_window)]
    if len(seed_window) == 0:
        return pd.Series(out, index=series.index)
    out[period - 1] = seed_window.mean()
    alpha = 1.0 / period
    for i in range(period, len(vals)):
        x = vals[i]
        if np.isnan(x):
            out[i] = out[i - 1]
            continue
        out[i] = alpha * x + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=series.index)


def calc_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Pine `ta.atr(length)` — SMMA of True Range. Requires High/Low/Close cols."""
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    close = df["Close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return calc_smma(tr, period)


def calc_rsi(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.rsi(src, length)` — Wilder RSI via RMA of gains/losses."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    rs = calc_smma(gain, period) / calc_smma(loss, period).replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def calc_adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Pine `ta.dmi(len, len)` ADX. Mirrors sweeps/2026-05-20/strat_bbmr.adx()."""
    h = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    c = df["Close"].values.astype(float)
    n = len(df)
    dm_p = np.zeros(n); dm_m = np.zeros(n); tr = np.zeros(n)
    for i in range(1, n):
        up = h[i] - h[i - 1]; dn = low[i - 1] - low[i]
        dm_p[i] = up if (up > dn and up > 0) else 0.0
        dm_m[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
    idx = df.index
    sp = calc_smma(pd.Series(dm_p, index=idx), period).values
    sm = calc_smma(pd.Series(dm_m, index=idx), period).values
    st = calc_smma(pd.Series(tr, index=idx), period).values
    dx = np.zeros(n)
    for i in range(n):
        if st[i] and not np.isnan(st[i]):
            dip = 100 * sp[i] / st[i]; dim = 100 * sm[i] / st[i]
            if dip + dim != 0:
                dx[i] = 100 * abs(dip - dim) / (dip + dim)
    return calc_smma(pd.Series(dx, index=idx), period)


def calc_zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score: (x - SMA) / sample-stdev. Mirrors strat_zscore.py."""
    mean = series.rolling(window).mean()
    std = series.rolling(window).std(ddof=1)
    return (series - mean) / std.replace(0, np.nan)
