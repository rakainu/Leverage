"""Candidate strategy families for SOL leverage scalping.

Each function takes a prepared OHLCV DataFrame + params and returns a list of
btengine.Signal. ALL features use only data available at the decision bar's
close (rolling windows / shift(1) channels) — no lookahead. The engine executes
on bar i+1.

Families:
  donchian_breakout   - trend: break of prior N-bar extreme, ATR stop, R-mult TP/trail
  zscore_fade         - mean reversion: fade rolling z-score extreme, revert to mean
  ema_pullback        - trend pullback: trade with EMA50/200 regime, enter on EMA touch
  adx_breakout        - momentum: Donchian break gated by ADX regime filter
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from btengine import Signal, ema, sma, atr, rsi, rolling_zscore, adx


def donchian_breakout(df, channel=20, sl_atr=2.0, tp_atr=3.0, atr_p=14,
                      trail=False, max_bars=0, entry="market", limit_atr=0.0,
                      min_atr_pct=0.0) -> list[Signal]:
    a = atr(df, atr_p)
    C, H, L = df["Close"], df["High"], df["Low"]
    upper = H.rolling(channel, min_periods=channel).max().shift(1)
    lower = L.rolling(channel, min_periods=channel).min().shift(1)
    long_brk = C > upper
    short_brk = C < lower
    atr_pct = a / C * 100.0
    sigs = []
    cv, av, uv, lv, lb, sb, ap = C.values, a.values, upper.values, lower.values, \
        long_brk.values, short_brk.values, atr_pct.values
    for i in range(len(df)):
        if np.isnan(av[i]) or np.isnan(uv[i]):
            continue
        if min_atr_pct > 0 and ap[i] < min_atr_pct:
            continue
        side = 1 if lb[i] else (-1 if sb[i] else 0)
        if side == 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl_atr * av[i],
                           tp_dist=(0.0 if trail else tp_atr * av[i]),
                           entry_style=entry, limit_dist=limit_atr * av[i],
                           max_bars=max_bars, trail_atr=(tp_atr * av[i] if trail else 0.0)))
    return sigs


def zscore_fade(df, z_period=20, z_entry=2.5, sl_atr=2.0, tp_mode="mean",
                tp_atr=2.0, atr_p=14, max_bars=48, entry="limit", limit_atr=0.25,
                trend_filter=0) -> list[Signal]:
    """Fade rolling z-score extremes. tp_mode 'mean' targets the SMA (mean reversion);
    'atr' uses a fixed R-multiple. Optional EMA200-slope trend filter to only fade
    counter to short-term overextension but with the larger trend (trend_filter=+1
    longs only in uptrend) — 0 disables."""
    C = df["Close"]
    a = atr(df, atr_p)
    z = rolling_zscore(C, z_period)
    mean = sma(C, z_period)
    e200 = ema(C, 200)
    cv, av, zv, mv, ev = C.values, a.values, z.values, mean.values, e200.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or np.isnan(mv[i]):
            continue
        side = 0
        if zv[i] <= -z_entry:
            side = 1
        elif zv[i] >= z_entry:
            side = -1
        if side == 0:
            continue
        if trend_filter != 0 and not np.isnan(ev[i]):
            up = cv[i] > ev[i]
            if trend_filter > 0 and ((side > 0) != up):
                continue
            if trend_filter < 0 and ((side > 0) != up):  # same gate, kept explicit
                continue
        if tp_mode == "mean":
            tp_dist = abs(mv[i] - cv[i])
            if tp_dist <= 0:
                continue
        else:
            tp_dist = tp_atr * av[i]
        sigs.append(Signal(i=i, side=side, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


def ema_pullback(df, fast=50, slow=200, sl_atr=2.0, tp_atr=3.0, atr_p=14,
                 touch_atr=0.5, slope_lookback=10, max_bars=0, entry="market",
                 trail=False) -> list[Signal]:
    """Trend pullback: regime from fast>slow EMA with rising fast EMA; enter long when
    price pulls back to within touch_atr*ATR of the fast EMA then closes back above it."""
    C, L, H = df["Close"], df["Low"], df["High"]
    ef, es, a = ema(C, fast), ema(C, slow), atr(df, atr_p)
    ef_slope = ef - ef.shift(slope_lookback)
    cv, lv, hv, efv, esv, av, slv = C.values, L.values, H.values, ef.values, es.values, a.values, ef_slope.values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(esv[i]) or np.isnan(av[i]) or np.isnan(slv[i]):
            continue
        up = efv[i] > esv[i] and slv[i] > 0
        dn = efv[i] < esv[i] and slv[i] < 0
        side = 0
        if up:
            # pullback: prior bar dipped near/below fast EMA, current bar closes above it
            if lv[i] <= efv[i] + touch_atr * av[i] and cv[i] > efv[i] and cv[i - 1] <= efv[i - 1] + touch_atr * av[i]:
                side = 1
        elif dn:
            if hv[i] >= efv[i] - touch_atr * av[i] and cv[i] < efv[i] and cv[i - 1] >= efv[i - 1] - touch_atr * av[i]:
                side = -1
        if side == 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl_atr * av[i],
                           tp_dist=(0.0 if trail else tp_atr * av[i]),
                           entry_style=entry, max_bars=max_bars,
                           trail_atr=(tp_atr * av[i] if trail else 0.0)))
    return sigs


def adx_breakout(df, channel=20, adx_min=20, adx_p=14, sl_atr=2.0, tp_atr=3.0,
                 atr_p=14, trail=False, max_bars=0, entry="market") -> list[Signal]:
    """Donchian breakout gated by ADX regime (only trade when trend strength high)."""
    a = atr(df, atr_p)
    adx_ = adx(df, adx_p)
    C, H, L = df["Close"], df["High"], df["Low"]
    upper = H.rolling(channel, min_periods=channel).max().shift(1)
    lower = L.rolling(channel, min_periods=channel).min().shift(1)
    cv, av, uv, lv, adv = C.values, a.values, upper.values, lower.values, adx_.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or np.isnan(uv[i]) or np.isnan(adv[i]):
            continue
        if adv[i] < adx_min:
            continue
        side = 1 if cv[i] > uv[i] else (-1 if cv[i] < lv[i] else 0)
        if side == 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl_atr * av[i],
                           tp_dist=(0.0 if trail else tp_atr * av[i]),
                           entry_style=entry, max_bars=max_bars,
                           trail_atr=(tp_atr * av[i] if trail else 0.0)))
    return sigs


def mr_fade(df, z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0, atr_p=14,
            adx_max=0, adx_p=14, rsi_p=14, rsi_os=0, rsi_ob=0, max_bars=48,
            limit_atr=0.25, trend_filter=0, side_only=0) -> list[Signal]:
    """Serious mean-reversion candidate.

    Long when z <= -z_entry (price stretched below its rolling mean); short when
    z >= +z_entry. Optional gates:
      adx_max  > 0 : only fade when ADX(adx_p) <= adx_max  (ranging regime only)
      rsi_os   > 0 : long only if RSI <= rsi_os ;  rsi_ob>0: short only if RSI >= rsi_ob
      trend_filter +1/-1 : only take trades aligned with EMA200 side (buy dips in uptrend)
      side_only +1/-1 : restrict to longs (+1) or shorts (-1) only
    Entry is a resting maker limit limit_atr*ATR beyond the decision close.
    TP targets a fraction tp_frac of the distance back to the rolling mean (1.0 = full
    reversion). SL = sl_atr*ATR. Time stop at max_bars.
    """
    C = df["Close"]
    a = atr(df, atr_p)
    z = rolling_zscore(C, z_period)
    mean = sma(C, z_period)
    e200 = ema(C, 200)
    adx_ = adx(df, adx_p) if adx_max > 0 else None
    rsi_ = rsi(C, rsi_p) if (rsi_os > 0 or rsi_ob > 0) else None
    cv, av, zv, mv, ev = C.values, a.values, z.values, mean.values, e200.values
    adv = adx_.values if adx_ is not None else None
    rv = rsi_.values if rsi_ is not None else None
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or np.isnan(mv[i]):
            continue
        side = 1 if zv[i] <= -z_entry else (-1 if zv[i] >= z_entry else 0)
        if side == 0:
            continue
        if side_only != 0 and side != side_only:
            continue
        if adv is not None and (np.isnan(adv[i]) or adv[i] > adx_max):
            continue
        if rv is not None and not np.isnan(rv[i]):
            if side > 0 and rsi_os > 0 and rv[i] > rsi_os:
                continue
            if side < 0 and rsi_ob > 0 and rv[i] < rsi_ob:
                continue
        if trend_filter != 0 and not np.isnan(ev[i]):
            up = cv[i] > ev[i]
            if (side > 0) != up:   # +1: longs only in uptrend, shorts only in downtrend
                continue
        # expected maker entry price, and TP a fraction of the way back to the mean
        exp_entry = cv[i] - limit_atr * av[i] if side > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(mv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit", limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


REGISTRY = {
    "donchian": donchian_breakout,
    "zfade": zscore_fade,
    "ema_pullback": ema_pullback,
    "adx_breakout": adx_breakout,
    "mr_fade": mr_fade,
}
