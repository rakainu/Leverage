"""Single-coin Donchian breakout family for the honest engine (momentum profile).

The OPPOSITE shape to the fade families: wide TP vs tight stop -> payoff>1, lower
win rate, PF carried by the winning tail. This is the structural escape from the
'small wins / big loss' problem of mean-reversion. Emits engine Signals; the
engine enforces no-lookahead + honest fills.

fn(df, side='both', **params) -> [Signal]
"""
from __future__ import annotations
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backtest"))
from engine import Signal, ema, atr, adx  # noqa: E402


def _allow(side_val, side):
    if side == "both":
        return True
    return side_val > 0 if side == "long" else side_val < 0


def efficiency_ratio(close, n):
    """Kaufman Efficiency Ratio: |net move over n| / sum|bar moves over n|.

    ~1.0 = clean directional trend, ~0.0 = chop. Causal (uses only bars <= i), so
    a value at bar i is a valid regime read for a decision made at i's close. This
    is the direct anti-chop gate: trade breakouts only when the tape is trending.
    """
    net = close.diff(n).abs()
    vol = close.diff().abs().rolling(n, min_periods=n).sum()
    return net / vol.replace(0.0, float("nan"))


def donchian_breakout(df, side="both", entry_n=20, sl_atr=1.5, tp_atr=3.0,
                      ema_len=100, ema_slope_lb=3, atr_p=14, vol_mult=0.0,
                      vol_sma=20, atr_min_pct=0.0, max_bars=0, trail_atr=0.0,
                      adx_min=0.0, adx_p=14, er_min=0.0, er_len=20):
    """Close breaks the prior `entry_n`-bar high (excl. current) above a rising EMA
    -> long; mirror for short. Hard ATR stop, ATR-multiple TP, optional ATR trail.
    Filters: EMA-trend alignment, optional volume expansion, optional ATR% floor.

    REGIME GATES (default off): adx_min requires ADX>=adx_min (trend strength);
    er_min requires Kaufman Efficiency Ratio>=er_min over er_len bars (trend vs
    chop). Both target the exact failure mode of raw breakouts — getting chopped up
    in ranging regimes."""
    H, L, C, V = df["High"], df["Low"], df["Close"], df["Volume"]
    a = atr(df, atr_p)
    e = ema(C, ema_len)
    don_hi = H.rolling(entry_n, min_periods=entry_n).max().shift(1)
    don_lo = L.rolling(entry_n, min_periods=entry_n).min().shift(1)
    volsma = V.rolling(vol_sma, min_periods=vol_sma).mean()
    atr_pct = a / C * 100.0
    e_rise = e > e.shift(ema_slope_lb)
    e_fall = e < e.shift(ema_slope_lb)
    adx_s = adx(df, adx_p) if adx_min > 0 else None
    er_s = efficiency_ratio(C, er_len) if er_min > 0 else None

    cv, av, ev = C.values, a.values, e.values
    dhi, dlo = don_hi.values, don_lo.values
    vv, vsv, apv = V.values, volsma.values, atr_pct.values
    er, ef = e_rise.values, e_fall.values
    adxv = adx_s.values if adx_s is not None else None
    erv = er_s.values if er_s is not None else None

    sigs = []
    for i in range(len(df)):
        if not np.isfinite(av[i]) or av[i] <= 0 or not np.isfinite(ev[i]):
            continue
        if not np.isfinite(dhi[i]) or not np.isfinite(dlo[i]):
            continue
        if atr_min_pct > 0 and (not np.isfinite(apv[i]) or apv[i] <= atr_min_pct):
            continue
        if vol_mult > 0 and (not np.isfinite(vsv[i]) or vsv[i] <= 0 or vv[i] <= vol_mult * vsv[i]):
            continue
        if adxv is not None and (not np.isfinite(adxv[i]) or adxv[i] < adx_min):
            continue  # regime: skip when trend strength is weak
        if erv is not None and (not np.isfinite(erv[i]) or erv[i] < er_min):
            continue  # regime: skip when the tape is choppy
        side_val = 0
        if cv[i] > dhi[i] and cv[i] > ev[i] and er[i]:
            side_val = 1
        elif cv[i] < dlo[i] and cv[i] < ev[i] and ef[i]:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style="market", max_bars=max_bars, trail_atr=trail_atr * av[i]))
    return sigs


DONCHIAN_DEFAULTS = dict(entry_n=20, sl_atr=1.5, tp_atr=3.0, ema_len=100,
                         vol_mult=1.2, atr_min_pct=0.0, max_bars=0, trail_atr=0.0)
