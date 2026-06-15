"""Momentum / breakout strategy families for the 15m aggressive day-trade hunt
(Approach A). Each returns a list of btengine.Signal — market entry, ATR hard stop,
ATR TRAILING stop (the lever that rides momentum), time stop, both sides. No fixed TP
(pure trail = let winners run). No lookahead: signal decided on bar i's close using
data up to i; engine works the order from bar i+1.

Optional ADX filter cuts chop (only take breakouts when trend strength is present)."""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30")))
from btengine import Signal, ema, sma, atr, rolling_zscore, adx  # noqa: E402


def _sig(i, side, av, sl_atr, trail_atr, max_bars):
    return Signal(i=i, side=side, sl_dist=sl_atr * av, tp_dist=0.0, entry_style="market",
                  max_bars=max_bars, trail_atr=trail_atr * av)


def _adx_ok(adxv, i, adx_min):
    return adx_min <= 0 or (np.isfinite(adxv[i]) and adxv[i] >= adx_min)


def donchian(df, side="both", lookback=40, sl_atr=1.5, trail_atr=2.5, max_bars=48,
             atr_p=14, adx_min=0.0):
    """Break the prior `lookback`-bar high/low, ride with the trail."""
    C = df["Close"].values
    a = atr(df, atr_p).values
    adxv = adx(df, 14).values
    hh = df["High"].rolling(lookback).max().shift(1).values
    ll = df["Low"].rolling(lookback).min().shift(1).values
    out = []
    for i in range(lookback + 20, len(df) - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or not _adx_ok(adxv, i, adx_min):
            continue
        s = 0
        if C[i] > hh[i]:
            s = 1
        elif C[i] < ll[i]:
            s = -1
        if s == 0 or (side == "long" and s < 0) or (side == "short" and s > 0):
            continue
        out.append(_sig(i, s, a[i], sl_atr, trail_atr, max_bars))
    return out


def vol_expansion(df, side="both", lookback=20, contract_win=30, contract_q=0.6,
                  sl_atr=1.5, trail_atr=2.5, max_bars=48, atr_p=14, adx_min=0.0):
    """Low-volatility contraction (ATR below its rolling median*q) then range breakout."""
    C = df["Close"].values
    a = atr(df, atr_p)
    med = a.rolling(contract_win).median()
    contracted = (a < contract_q * med)
    av = a.values; cz = contracted.values
    adxv = adx(df, 14).values
    hh = df["High"].rolling(lookback).max().shift(1).values
    ll = df["Low"].rolling(lookback).min().shift(1).values
    out = []
    for i in range(max(lookback, contract_win) + 20, len(df) - 1):
        if not np.isfinite(av[i]) or av[i] <= 0 or not _adx_ok(adxv, i, adx_min):
            continue
        # require recent contraction in the last few bars
        if not cz[max(0, i - 3):i + 1].any():
            continue
        s = 0
        if C[i] > hh[i]:
            s = 1
        elif C[i] < ll[i]:
            s = -1
        if s == 0 or (side == "long" and s < 0) or (side == "short" and s > 0):
            continue
        out.append(_sig(i, s, av[i], sl_atr, trail_atr, max_bars))
    return out


def roc_momentum(df, side="both", roc_p=8, zwin=50, z_thr=1.8, sl_atr=1.5, trail_atr=2.5,
                 max_bars=48, atr_p=14, adx_min=0.0):
    """Z-score of rate-of-change crosses a threshold -> ride the momentum impulse."""
    C = df["Close"]
    roc = C.pct_change(roc_p)
    z = rolling_zscore(roc, zwin).values
    a = atr(df, atr_p).values
    adxv = adx(df, 14).values
    out = []
    prev = None
    for i in range(zwin + roc_p + 20, len(df) - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or not np.isfinite(z[i]):
            prev = z[i]; continue
        s = 0
        # fresh cross of the threshold (impulse), not staying-above
        if prev is not None and z[i] >= z_thr and prev < z_thr:
            s = 1
        elif prev is not None and z[i] <= -z_thr and prev > -z_thr:
            s = -1
        prev = z[i]
        if s == 0 or not _adx_ok(adxv, i, adx_min):
            continue
        if (side == "long" and s < 0) or (side == "short" and s > 0):
            continue
        out.append(_sig(i, s, a[i], sl_atr, trail_atr, max_bars))
    return out


def ema_momentum(df, side="both", fast=20, slow=50, fresh=10, sl_atr=1.5, trail_atr=2.5,
                 max_bars=48, atr_p=14, adx_min=0.0):
    """Trend by fast/slow EMA; enter on a FRESH `fresh`-bar close extreme in trend dir."""
    C = df["Close"]
    ef = ema(C, fast).values; es = ema(C, slow).values
    cv = C.values
    hh = C.rolling(fresh).max().shift(1).values
    ll = C.rolling(fresh).min().shift(1).values
    a = atr(df, atr_p).values
    adxv = adx(df, 14).values
    out = []
    for i in range(slow + fresh + 20, len(df) - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or not _adx_ok(adxv, i, adx_min):
            continue
        s = 0
        if ef[i] > es[i] and cv[i] > hh[i]:
            s = 1
        elif ef[i] < es[i] and cv[i] < ll[i]:
            s = -1
        if s == 0 or (side == "long" and s < 0) or (side == "short" and s > 0):
            continue
        out.append(_sig(i, s, a[i], sl_atr, trail_atr, max_bars))
    return out


def pullback_trend(df, side="both", fast=20, slow=50, slope_lb=20, sl_atr=1.5,
                   trail_atr=2.5, max_bars=48, atr_p=14, adx_min=0.0):
    """Trend-following with a MEAN-REVERSION entry: in an uptrend (fast>slow, slow
    rising), buy the DIP — price pulls below the fast EMA then RECLAIMS it. Short the
    rip in a downtrend. Enters on weakness within strength = far better fills than
    chasing the breakout."""
    C = df["Close"]
    ef = ema(C, fast).values
    es = ema(C, slow)
    es_slope = (es - es.shift(slope_lb)).values
    es = es.values
    cv = C.values
    a = atr(df, atr_p).values
    adxv = adx(df, 14).values
    out = []
    for i in range(slow + slope_lb + 20, len(df) - 1):
        if not np.isfinite(a[i]) or a[i] <= 0 or not _adx_ok(adxv, i, adx_min):
            continue
        s = 0
        up = ef[i] > es[i] and es_slope[i] > 0
        dn = ef[i] < es[i] and es_slope[i] < 0
        # reclaim of the fast EMA after a dip below (long) / rip above (short)
        if up and cv[i] > ef[i] and cv[i - 1] <= ef[i - 1]:
            s = 1
        elif dn and cv[i] < ef[i] and cv[i - 1] >= ef[i - 1]:
            s = -1
        if s == 0 or (side == "long" and s < 0) or (side == "short" and s > 0):
            continue
        out.append(_sig(i, s, a[i], sl_atr, trail_atr, max_bars))
    return out


FAMILIES = {
    "donchian": donchian,
    "vol_expansion": vol_expansion,
    "roc_momentum": roc_momentum,
    "ema_momentum": ema_momentum,
    "pullback_trend": pullback_trend,
}
