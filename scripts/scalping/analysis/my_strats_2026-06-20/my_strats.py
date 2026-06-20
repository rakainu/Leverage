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
from btengine import Signal, atr, ema, rolling_zscore  # noqa: E402
from strat_lib import session_vwap  # noqa: E402


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


REGISTRY = {
    "crash_snap": crash_snap,
    "decel_fade": decel_fade,
}
