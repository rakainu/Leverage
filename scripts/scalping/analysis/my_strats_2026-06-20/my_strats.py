"""My own intraday strategies — built from scratch, tested on the honest engine.

Design rules baked in from everything we learned:
  - edge must be in DIRECTION, not a precise intrabar price (no phantom fills);
  - market-achievable entries (market next-open, or a maker limit that really fills);
  - intraday only (small max_bars — nothing held overnight);
  - never fade a move that is still ACCELERATING (the scalper's killer);
  - zero-fee Lighter costs; risk-fractional sizing; honest causal simulate().

Each fn(df, side='both', **p) -> list[Signal], 1:1 with strat_lib's contract.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
sys.path.insert(0, os.path.join(HERE, "..", "scalp_search_2026-05-30"))
from btengine import Signal, atr, ema, sma, rolling_zscore  # noqa: E402
from strat_lib import session_vwap  # noqa: E402


# --- standard building blocks used by the confluence strategy ---
def wavetrend(df, n1=10, n2=21):
    """LazyBear WaveTrend oscillator (wt1, wt2)."""
    hlc3 = (df["High"] + df["Low"] + df["Close"]) / 3.0
    esa = ema(hlc3, n1)
    d = ema((hlc3 - esa).abs(), n1)
    ci = (hlc3 - esa) / (0.015 * d.replace(0, np.nan))
    wt1 = ema(ci, n2)
    wt2 = wt1.rolling(4, min_periods=4).mean()
    return wt1, wt2


def squeeze_release(df, bb_len=20, bb_mult=2.0, kc_mult=1.5):
    """TTM-style squeeze: True on the bar a squeeze (BB inside KC) RELEASES."""
    C = df["Close"]
    basis = sma(C, bb_len); dev = bb_mult * C.rolling(bb_len, min_periods=bb_len).std(ddof=0)
    ub, lb = basis + dev, basis - dev
    a = atr(df, bb_len)
    ma = sma(C, bb_len)
    ukc, lkc = ma + a * kc_mult, ma - a * kc_mult
    on = (lb > lkc) & (ub < ukc)        # bands compressed inside Keltner
    return on.shift(1).fillna(False) & ~on.fillna(False)   # was on, now off = release


def _allow(side_val, side):
    if side == "both":
        return True
    return side_val > 0 if side == "long" else side_val < 0


# ---------------------------------------------------------------------------
# 1) CRASH-AND-SNAP: a volatility-climax bar (range >= mult*ATR) tends to
#    overshoot then snap back. Fade the climax, but place the stop BEYOND the
#    climax extreme (the wick) + a buffer — so you're only stopped if the climax
#    low/high actually breaks (the snap genuinely failed), not by normal noise.
#    Maker limit a touch beyond the close (expect a little more flush, better fill).
# ---------------------------------------------------------------------------
def crash_snap(df, side="both", range_mult=2.5, atr_p=14, limit_atr=0.10,
               sl_buf=0.5, tp_frac=0.5, max_bars=8, min_body_frac=0.5):
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    ov, hv, lv, cv, av = O.values, H.values, L.values, C.values, a.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0:
            continue
        rng = hv[i] - lv[i]
        if rng <= 0 or rng < range_mult * av[i]:
            continue
        body = abs(cv[i] - ov[i])
        if body < min_body_frac * rng:          # need a decisive directional flush
            continue
        down = cv[i] < ov[i]                     # selling climax -> fade LONG
        side_val = 1 if down else -1
        if not _allow(side_val, side):
            continue
        limit_off = limit_atr * av[i]
        if side_val > 0:
            entry = cv[i] - limit_off            # buy a touch lower
            sl_dist = (entry - lv[i]) + sl_buf * av[i]   # stop below the climax low
        else:
            entry = cv[i] + limit_off
            sl_dist = (hv[i] - entry) + sl_buf * av[i]
        tp_dist = tp_frac * rng                  # target a fraction of the flush back
        if sl_dist <= 0 or tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp_dist,
                           entry_style="limit", limit_dist=limit_off, max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
# 2) DECELERATION FADE: fade a stretch from session VWAP — but ONLY once the
#    move is losing steam (this bar's range < prior bar's range) AND turning
#    back (close moved back toward VWAP vs the prior close). This is the
#    scalper's fade with the "never fade acceleration" lesson built in from the
#    start: we wait for the rip to stall before fading it.
# ---------------------------------------------------------------------------
def decel_fade(df, side="both", z_period=30, z_entry=1.8, atr_p=14, sl_atr=1.5,
               tp_frac=0.5, limit_atr=0.15, max_bars=10, require_decel=True):
    C = df["Close"]; a = atr(df, atr_p); vwap = session_vwap(df)
    z = rolling_zscore(C - vwap, z_period)
    cv, av, vv, zv = C.values, a.values, vwap.values, z.values
    hv, lv = df["High"].values, df["Low"].values
    sigs = []
    for i in range(2, len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(vv[i]):
            continue
        rng_now = hv[i] - lv[i]
        rng_prev = hv[i - 1] - lv[i - 1]
        decel = rng_now < rng_prev               # momentum cooling
        if require_decel and not decel:
            continue
        # stretched ABOVE vwap + closing back down vs prior close -> fade SHORT
        if zv[i] >= z_entry and cv[i] < cv[i - 1]:
            side_val = -1
        # stretched BELOW vwap + closing back up -> fade LONG
        elif zv[i] <= -z_entry and cv[i] > cv[i - 1]:
            side_val = 1
        else:
            continue
        if not _allow(side_val, side):
            continue
        limit_off = limit_atr * av[i]
        entry = cv[i] - limit_off if side_val > 0 else cv[i] + limit_off
        tp_dist = abs(vv[i] - entry) * tp_frac   # target a fraction of the way back to vwap
        sl_dist = sl_atr * av[i]
        if tp_dist <= 0 or sl_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp_dist,
                           entry_style="limit", limit_dist=limit_off, max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
# 3) BREAKOUT-AND-RIDE (momentum — the OPPOSITE of fading): price closes beyond
#    the prior `lookback`-bar high/low by a buffer -> go WITH the move, market
#    entry, ATR stop, and a trailing stop so winners run (no fixed TP). This is
#    the sleeve that should PROFIT from the news rips that hurt the fade scalper.
#    Optional volume confirmation (vol_mult>0: breakout bar volume vs its average).
# ---------------------------------------------------------------------------
def breakout_hold(df, side="both", lookback=20, atr_p=14, sl_atr=1.5, trail_mult=2.0,
                  buf_atr=0.1, max_bars=24, vol_mult=0.0):
    H, L, C, V = df["High"], df["Low"], df["Close"], df["Volume"]
    a = atr(df, atr_p)
    hh = H.rolling(lookback, min_periods=lookback).max().shift(1)
    ll = L.rolling(lookback, min_periods=lookback).min().shift(1)
    volma = V.rolling(lookback, min_periods=lookback).mean()
    cv, av, hhv, llv, vv, vmv = C.values, a.values, hh.values, ll.values, V.values, volma.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(hhv[i]) or np.isnan(llv[i]):
            continue
        if vol_mult > 0 and (np.isnan(vmv[i]) or vv[i] < vol_mult * vmv[i]):
            continue
        if cv[i] > hhv[i] + buf_atr * av[i]:
            side_val = 1
        elif cv[i] < llv[i] - buf_atr * av[i]:
            side_val = -1
        else:
            continue
        if not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=0.0,
                           entry_style="market", trail_atr=trail_mult * av[i],
                           max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
# 4) VOLUME-THRUST: a bar with a volume spike (>= vol_mult x its average) AND a
#    decisive directional body -> momentum tends to continue near-term. Go WITH
#    it, trail. Different trigger from breakout_hold (volume/body, not a level).
# ---------------------------------------------------------------------------
def vol_thrust(df, side="both", atr_p=14, vol_lb=20, vol_mult=2.0, body_frac=0.5,
               sl_atr=1.5, trail_mult=2.0, max_bars=16):
    O, H, L, C, V = df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]
    a = atr(df, atr_p)
    volma = V.rolling(vol_lb, min_periods=vol_lb).mean()
    ov, hv, lv, cv, av, vv, vmv = (O.values, H.values, L.values, C.values,
                                   a.values, V.values, volma.values)
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(vmv[i]) or vmv[i] <= 0:
            continue
        if vv[i] < vol_mult * vmv[i]:
            continue
        rng = hv[i] - lv[i]
        if rng <= 0:
            continue
        body = abs(cv[i] - ov[i])
        if body < body_frac * rng:
            continue
        side_val = 1 if cv[i] > ov[i] else -1
        if not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=0.0,
                           entry_style="market", trail_atr=trail_mult * av[i],
                           max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
# 5) CONFLUENCE (QuantCrawler-style concept): enter WITH the trend only when
#    several independent signals agree — EMA trend + WaveTrend momentum +
#    squeeze release + volume. min_conf = how many of the 4 must align. Exits
#    are the screenshot's scale-out: TP1 at tp1_r (book half), TP2 at tp2_r
#    (rest), move stop to breakeven after TP1, ATR hard stop. Built to be SWEPT
#    across settings — a real edge survives a range, a curve-fit one needs one
#    magic combo.
# ---------------------------------------------------------------------------
def confluence(df, side="both", ema_fast=21, ema_slow=50, wt_n1=10, wt_n2=21,
               vol_lb=20, vol_mult=1.2, min_conf=3, sl_atr=1.5, tp1_r=2.2,
               tp2_r=4.0, max_bars=24, require_squeeze=False):
    C = df["Close"]; a = atr(df, 14)
    ef, es = ema(C, ema_fast), ema(C, ema_slow)
    wt1, wt2 = wavetrend(df, wt_n1, wt_n2)
    rel = squeeze_release(df).values
    volma = df["Volume"].rolling(vol_lb, min_periods=vol_lb).mean()
    cv, av = C.values, a.values
    efv, esv, w1, w2 = ef.values, es.values, wt1.values, wt2.values
    vv, vmv = df["Volume"].values, volma.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(esv[i]) or np.isnan(w2[i]) or np.isnan(vmv[i]):
            continue
        trend = 1 if efv[i] > esv[i] else -1
        mom = 1 if w1[i] > w2[i] else -1
        sqz = bool(rel[i])
        vol_ok = vv[i] >= vol_mult * vmv[i]
        # confluence count in the trend's direction
        score = 1 + (1 if mom == trend else 0) + (1 if sqz else 0) + (1 if vol_ok else 0)
        if require_squeeze and not sqz:
            continue
        if mom != trend:                      # momentum must not oppose the trend
            continue
        if score < min_conf:
            continue
        side_val = trend
        if not _allow(side_val, side):
            continue
        sl_dist = sl_atr * av[i]
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp1_r * sl_dist,
                           entry_style="market", max_bars=max_bars,
                           tp1_frac=0.5, tp2_dist=tp2_r * sl_dist, be_after_tp1=True))
    return sigs


REGISTRY = {
    "crash_snap": crash_snap,
    "decel_fade": decel_fade,
    "breakout_hold": breakout_hold,
    "vol_thrust": vol_thrust,
    "confluence": confluence,
}
