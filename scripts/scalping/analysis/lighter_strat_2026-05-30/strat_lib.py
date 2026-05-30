"""Strategy library for the Lighter zero-fee search (2026-05-30).

Six testable families, all LONG+SHORT capable, all with a real hard ATR stop and
maker-limit-or-market entry. They reuse the honest engine in the sibling folder
(sol_strategy_2026-05-30/btengine.py): signals decided on bar close, executed next
bar, no lookahead.

Each fn(df, side='both', **params) -> list[Signal].  side in {'both','long','short'}.

Families:
  range_fade        - fade the edges of a Donchian range in low-ADX regimes
  failed_breakout   - break prior extreme then close back inside -> fade it
  sweep_reversal    - wick through prior swing extreme then reclaim -> reverse
  squeeze_expansion - Bollinger-in-Keltner compression -> trade the release
  reclaim_pullback  - in trend, pull back to fast EMA then reclaim it (not a crossover)
  mr_fade2          - z-score mean reversion (general both-sided; prior family, re-tested)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sol_strategy_2026-05-30"))
from btengine import Signal, ema, sma, atr, rsi, rolling_zscore, adx  # noqa: E402


def _allow(side_val, side):
    if side == "both":
        return True
    if side == "long":
        return side_val > 0
    return side_val < 0


# ---------------------------------------------------------------------------
def range_fade(df, side="both", lookback=40, edge_frac=0.10, adx_max=30, adx_p=14,
               sl_atr=1.5, tp_to="mid", atr_p=14, max_bars=48, limit_atr=0.0,
               min_width_atr=2.0):
    """Fade the edges of an established range. Long near range low, short near
    range high, only when ADX<=adx_max (ranging) and the range is wide enough."""
    H, L, C = df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    hh = H.rolling(lookback, min_periods=lookback).max().shift(1)
    ll = L.rolling(lookback, min_periods=lookback).min().shift(1)
    mid = (hh + ll) / 2.0
    width = hh - ll
    adx_ = adx(df, adx_p)
    cv, av, hv, lv, mv, wv, dv = C.values, a.values, hh.values, ll.values, mid.values, width.values, adx_.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(hv[i]) or np.isnan(av[i]) or np.isnan(dv[i]) or av[i] <= 0:
            continue
        if dv[i] > adx_max:
            continue
        if wv[i] < min_width_atr * av[i]:
            continue
        band = edge_frac * wv[i]
        side_val = 0
        if cv[i] <= lv[i] + band:
            side_val = 1
        elif cv[i] >= hv[i] - band:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        target = mv[i] if tp_to == "mid" else (hv[i] if side_val > 0 else lv[i])
        tp_dist = abs(target - exp_entry)
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr >= 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def failed_breakout(df, side="both", lookback=20, sl_atr=1.0, tp_atr=2.0, atr_p=14,
                    max_bars=48, entry="market", limit_atr=0.0, confirm_close=True):
    """Price pokes beyond the prior N-bar extreme then closes back inside -> fade
    the failed breakout. Long on a failed breakdown, short on a failed breakout."""
    H, L, C = df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    prior_hi = H.rolling(lookback, min_periods=lookback).max().shift(1)
    prior_lo = L.rolling(lookback, min_periods=lookback).min().shift(1)
    cv, av, hv, lv, phi, plo = C.values, a.values, H.values, L.values, prior_hi.values, prior_lo.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(phi[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        side_val = 0
        # failed breakout up: high poked above prior_hi, close back below it
        if hv[i] > phi[i] and cv[i] < phi[i]:
            side_val = -1
        # failed breakdown: low poked below prior_lo, close back above it
        elif lv[i] < plo[i] and cv[i] > plo[i]:
            side_val = 1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def sweep_reversal(df, side="both", lookback=20, sl_atr=1.0, tp_atr=2.0, atr_p=14,
                   max_bars=48, entry="market", limit_atr=0.0, wick_atr=0.0):
    """Liquidity sweep + reclaim. A bar's wick pierces the prior swing extreme
    (stop hunt) but closes back inside -> reverse. Stop just beyond the sweep wick."""
    H, L, C = df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    prior_hi = H.rolling(lookback, min_periods=lookback).max().shift(1)
    prior_lo = L.rolling(lookback, min_periods=lookback).min().shift(1)
    cv, av, hv, lv, phi, plo = C.values, a.values, H.values, L.values, prior_hi.values, prior_lo.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(phi[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        side_val = 0
        sl_dist = sl_atr * av[i]
        # sweep of lows -> long ; require wick depth >= wick_atr*ATR below prior_lo
        if lv[i] < plo[i] - wick_atr * av[i] and cv[i] > plo[i]:
            side_val = 1
            sl_dist = (cv[i] - lv[i]) + sl_atr * av[i]  # stop below the sweep wick
        elif hv[i] > phi[i] + wick_atr * av[i] and cv[i] < phi[i]:
            side_val = -1
            sl_dist = (hv[i] - cv[i]) + sl_atr * av[i]
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def squeeze_expansion(df, side="both", bb_len=20, bb_mult=2.0, kc_mult=1.5, atr_p=14,
                      sl_atr=1.5, tp_atr=3.0, max_bars=48, entry="market", min_squeeze=6,
                      trail=False):
    """Bollinger-in-Keltner volatility compression, then trade the expansion in the
    direction of the release (momentum sign at release)."""
    C = df["Close"]
    a = atr(df, atr_p)
    basis = sma(C, bb_len)
    dev = C.rolling(bb_len, min_periods=bb_len).std(ddof=0)
    upper_bb = basis + bb_mult * dev
    lower_bb = basis - bb_mult * dev
    upper_kc = basis + kc_mult * a
    lower_kc = basis - kc_mult * a
    squeeze = (upper_bb < upper_kc) & (lower_bb > lower_kc)  # BB inside KC
    mom = C - basis
    sq = squeeze.values; cv, av, bv, mvv = C.values, a.values, basis.values, mom.values
    run = 0
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(bv[i]):
            run = 0
            continue
        if sq[i]:
            run += 1
            continue
        # squeeze just released this bar after >= min_squeeze bars on
        if run >= min_squeeze:
            side_val = 1 if mvv[i] > 0 else -1
            if _allow(side_val, side):
                sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i],
                                   tp_dist=(0.0 if trail else tp_atr * av[i]),
                                   entry_style=entry, max_bars=max_bars,
                                   trail_atr=(tp_atr * av[i] if trail else 0.0)))
        run = 0
    return sigs


# ---------------------------------------------------------------------------
def reclaim_pullback(df, side="both", fast=20, slow=100, sl_atr=1.5, tp_atr=3.0,
                     atr_p=14, max_bars=48, entry="market", slope_lb=10, trail=False):
    """Trend pullback-and-reclaim (NOT a crossover entry). In an uptrend (fast>slow,
    fast rising), wait for price to dip below fast EMA then CLOSE back above it."""
    C, H, L = df["Close"], df["High"], df["Low"]
    ef, es, a = ema(C, fast), ema(C, slow), atr(df, atr_p)
    slope = ef - ef.shift(slope_lb)
    cv, lv, hv, efv, esv, av, slv = C.values, L.values, H.values, ef.values, es.values, a.values, slope.values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(esv[i]) or np.isnan(av[i]) or np.isnan(slv[i]) or av[i] <= 0:
            continue
        side_val = 0
        up = efv[i] > esv[i] and slv[i] > 0
        dn = efv[i] < esv[i] and slv[i] < 0
        if up and lv[i - 1] < efv[i - 1] and cv[i] > efv[i] and cv[i - 1] <= cv[i]:
            side_val = 1
        elif dn and hv[i - 1] > efv[i - 1] and cv[i] < efv[i] and cv[i - 1] >= cv[i]:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i],
                           tp_dist=(0.0 if trail else tp_atr * av[i]),
                           entry_style=entry, max_bars=max_bars,
                           trail_atr=(tp_atr * av[i] if trail else 0.0)))
    return sigs


# ---------------------------------------------------------------------------
def mr_fade2(df, side="both", z_period=20, z_entry=2.5, sl_atr=2.0, tp_frac=1.0,
             atr_p=14, adx_max=0, adx_p=14, max_bars=48, limit_atr=0.0):
    """z-score mean reversion, both-sided (prior family, re-tested honestly)."""
    C = df["Close"]; a = atr(df, atr_p); z = rolling_zscore(C, z_period); mean = sma(C, z_period)
    adx_ = adx(df, adx_p) if adx_max > 0 else None
    cv, av, zv, mv = C.values, a.values, z.values, mean.values
    adv = adx_.values if adx_ is not None else None
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or np.isnan(mv[i]) or av[i] <= 0:
            continue
        side_val = 1 if zv[i] <= -z_entry else (-1 if zv[i] >= z_entry else 0)
        if side_val == 0 or not _allow(side_val, side):
            continue
        if adv is not None and (np.isnan(adv[i]) or adv[i] > adx_max):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(mv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit", limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


REGISTRY = {
    "range_fade": range_fade,
    "failed_breakout": failed_breakout,
    "sweep_reversal": sweep_reversal,
    "squeeze_expansion": squeeze_expansion,
    "reclaim_pullback": reclaim_pullback,
    "mr_fade2": mr_fade2,
}
