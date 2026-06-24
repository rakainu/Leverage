"""V3 Pine signal regeneration. Mirrors sweeps/2026-05-20/strategy.py.

Computes the V3 buy/sell signals + EMA(9) + slope + body/ATR ratio on a
DataFrame of OHLCV bars. Used by main.py to detect new signals as fresh
bars close.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import calc_ema, calc_atr, calc_smma


def generate_v3_signals(
    df: pd.DataFrame,
    sensitivity: int = 8,
    noise: float = 0.0,
    fakeout: float = 0.2,
    range_filt: float = 0.2,
) -> pd.DataFrame:
    """Replicate the V3 Pine: HA-smoothed flip + body/ATR fakeout + ADX range filter."""
    df = df.copy()
    closes = df["Close"].values.astype(float)
    opens = df["Open"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(df)

    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.zeros(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    smooth_len = 16 - sensitivity
    if smooth_len <= 1:
        smoothed = ha_close.copy()
    else:
        smoothed = calc_ema(pd.Series(ha_close, index=df.index), smooth_len).values

    ha_bull = np.zeros(n, dtype=bool)
    ha_bear = np.zeros(n, dtype=bool)
    for i in range(1, n):
        ha_bull[i] = smoothed[i] > smoothed[i - 1]
        ha_bear[i] = smoothed[i] < smoothed[i - 1]

    atr14 = calc_atr(df, 14).values
    fakeout_pass = np.ones(n, dtype=bool)
    if fakeout > 0:
        body = np.abs(closes - opens)
        valid = ~np.isnan(atr14)
        fakeout_pass[valid] = body[valid] > fakeout * atr14[valid]

    # ADX (Wilder)
    dm_p = np.zeros(n)
    dm_m = np.zeros(n)
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        dm_p[i] = up if (up > dn and up > 0) else 0
        dm_m[i] = dn if (dn > up and dn > 0) else 0
    sdm_p = calc_smma(pd.Series(dm_p, index=df.index), 14).values
    sdm_m = calc_smma(pd.Series(dm_m, index=df.index), 14).values
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]))
    str_ = calc_smma(pd.Series(tr, index=df.index), 14).values
    dx = np.zeros(n)
    for i in range(n):
        if str_[i] != 0 and not np.isnan(str_[i]):
            dip = sdm_p[i] / str_[i] * 100
            dim = sdm_m[i] / str_[i] * 100
            if dip + dim != 0:
                dx[i] = abs(dip - dim) / (dip + dim) * 100
    adx = calc_smma(pd.Series(dx, index=df.index), 14).values
    range_pass = np.ones(n, dtype=bool)
    if range_filt > 0:
        threshold = 20.0 * range_filt
        valid = ~np.isnan(adx)
        range_pass[valid] = adx[valid] > threshold

    buy_sig = np.zeros(n, dtype=bool)
    sell_sig = np.zeros(n, dtype=bool)
    for i in range(1, n):
        buy_sig[i] = ha_bull[i] and not ha_bull[i - 1] and fakeout_pass[i] and range_pass[i]
        sell_sig[i] = ha_bear[i] and not ha_bear[i - 1] and fakeout_pass[i] and range_pass[i]

    df["buy_sig"] = buy_sig
    df["sell_sig"] = sell_sig
    df["adx"] = adx
    df["body_atr_ratio"] = np.where(atr14 > 0, np.abs(closes - opens) / atr14, 0)
    df["atr14"] = atr14
    return df


def compute_ema_and_slope(df: pd.DataFrame, ema_period: int = 9,
                          slope_lookback: int = 3) -> pd.DataFrame:
    """Add ema9 + slope_pct columns (used by entry gate)."""
    df = df.copy()
    ema = calc_ema(df["Close"], ema_period).values
    n = len(df)
    slope_pct = np.zeros(n)
    for i in range(slope_lookback, n):
        prev = ema[i - slope_lookback]
        if prev and not np.isnan(prev) and not np.isnan(ema[i]):
            slope_pct[i] = (ema[i] - prev) / prev * 100.0
    df["ema9"] = ema
    df["slope_pct"] = slope_pct
    return df


def prepare(df: pd.DataFrame, sensitivity=8, noise=0.0, fakeout=0.2,
            range_filt=0.2, ema_period=9, slope_lookback=3) -> pd.DataFrame:
    """One-shot: run Pine signals + ema/slope. Returns enriched DataFrame."""
    df = generate_v3_signals(df, sensitivity, noise, fakeout, range_filt)
    df = compute_ema_and_slope(df, ema_period, slope_lookback)
    return df


def passes_entry_filters(
    ts: pd.Timestamp,
    slope_pct: float,
    body_atr: float,
    block_weekdays: list[int],
    min_abs_slope_pct: float,
    block_body_band: tuple[float, float] | None,
) -> bool:
    """Apply the locked-config entry filters. Returns True if the signal should fire."""
    if block_weekdays and ts.weekday() in block_weekdays:
        return False
    if min_abs_slope_pct and abs(slope_pct) < min_abs_slope_pct:
        return False
    if block_body_band:
        lo, hi = block_body_band
        if lo <= body_atr < hi:
            return False
    return True


def check_retest(side: str, ema_val: float, bar_low: float, bar_high: float,
                 overshoot_pct: float = 0.2) -> bool:
    """EMA(9) retest condition — bar touched or briefly broke EMA on the right side."""
    if np.isnan(ema_val):
        return False
    overshoot = ema_val * (overshoot_pct / 100.0)
    if side == "long":
        return bar_low <= ema_val and bar_low >= ema_val - overshoot
    return bar_high >= ema_val and bar_high <= ema_val + overshoot


def check_reclaim(side: str, ema_val: float, bar_close: float) -> bool:
    """Reclaim condition (M13) — after wicking to EMA9 the bar must CLOSE BACK on
    the trade's side of EMA9: a confirmed bounce, not a breakdown. Long closes
    above EMA9; short closes below. This is what distinguishes the winning
    reclaim from the losing knife-through."""
    if np.isnan(ema_val):
        return False
    return bar_close > ema_val if side == "long" else bar_close < ema_val


def entry_gap_pct(ema_val: float, bar_close: float) -> float:
    """How far the reclaim close sits from EMA9, in percent. The realizable
    entry's distance from the engine's idealized EMA9 fill — the cost the gap
    filter caps. Returns a large number if EMA9 is invalid (forces a skip)."""
    if np.isnan(ema_val) or ema_val == 0:
        return float("inf")
    return abs(bar_close - ema_val) / ema_val * 100.0
