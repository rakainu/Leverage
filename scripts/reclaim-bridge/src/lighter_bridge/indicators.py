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


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.sma(src, length)`."""
    return series.rolling(period, min_periods=period).mean()


def calc_stdev(series: pd.Series, period: int) -> pd.Series:
    """Pine `ta.stdev(src, length)` — population stdev (ddof=0), matching the
    squeeze backtest (strat_lib.squeeze_expansion uses std(ddof=0))."""
    return series.rolling(period, min_periods=period).std(ddof=0)


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
