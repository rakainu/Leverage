"""Scalping strategy library — aggressive high-frequency edge search (2026-05-30).

All families are LONG+SHORT capable, decide on bar close, execute next bar (no
lookahead), and carry a real hard ATR stop. Built for the honest engine in
sol_strategy_2026-05-30/btengine.py and Lighter zero-fee costs.

Design bias: FREQUENCY. Thresholds default loose enough to fire many times per
day on 1m/3m; the backtest is the safety filter, not the threshold.

fn(df, side='both', **params) -> list[Signal].  side in {'both','long','short'}.

Families (this file adds the scalping-specific ones; the 6 from
lighter_strat_2026-05-30/strat_lib.py are imported into the registry too):
  bb_revert        - close pierces a Bollinger band -> fade back toward basis
  kc_revert        - close pierces a Keltner band -> fade back toward basis
  rsi_snapback     - RSI crosses back out of an extreme -> fade
  stoch_snapback   - Stochastic %K crosses back out of an extreme -> fade
  vwap_revert      - price extended N*sigma from session VWAP -> fade to VWAP
  vwap_reclaim     - price reclaims/loses session VWAP -> follow the reclaim
  wick_fade        - single-bar long rejection wick -> fade the wick (stop-run)
  atr_climax_fade  - bar range >> ATR (volatility climax) -> fade the close
  micro_pullback   - strong impulse + shallow 1-2 bar pullback -> continue
  orb_fade         - opening-range break that fails -> fade back into the range
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

import importlib.util

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "sol_strategy_2026-05-30"))
from btengine import Signal, ema, sma, atr, rsi, rolling_zscore, adx  # noqa: E402

# reuse the six prior families verbatim — load by explicit path to avoid the
# name collision with THIS file (both are strat_lib.py).
_prior_path = os.path.join(HERE, "..", "lighter_strat_2026-05-30", "strat_lib.py")
_spec = importlib.util.spec_from_file_location("prior_strat_lib", _prior_path)
_prior = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_prior)


def _allow(side_val, side):
    if side == "both":
        return True
    if side == "long":
        return side_val > 0
    return side_val < 0


# ---------------------------------------------------------------------------
# Extra indicators not in btengine
# ---------------------------------------------------------------------------

def bollinger(C, length=20, mult=2.0):
    basis = sma(C, length)
    dev = C.rolling(length, min_periods=length).std(ddof=0)
    return basis, basis + mult * dev, basis - mult * dev


def stoch_k(df, k_len=14, smooth=3):
    H, L, C = df["High"], df["Low"], df["Close"]
    hh = H.rolling(k_len, min_periods=k_len).max()
    ll = L.rolling(k_len, min_periods=k_len).min()
    raw = 100 * (C - ll) / (hh - ll).replace(0, np.nan)
    return raw.rolling(smooth, min_periods=smooth).mean()


def session_vwap(df):
    """Daily-anchored VWAP + rolling std of price-from-vwap (UTC day reset)."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, np.nan).ffill().fillna(1.0)
    day = df.index.normalize()
    pv = (tp * vol).groupby(day).cumsum()
    vv = vol.groupby(day).cumsum()
    vwap = pv / vv
    return vwap


# ---------------------------------------------------------------------------
def bb_revert(df, side="both", length=20, mult=2.0, sl_atr=1.5, tp_frac=1.0,
              atr_p=14, max_bars=24, limit_atr=0.0, adx_max=0, adx_p=14,
              require_close_back=False):
    """Bollinger extreme reversion. Close below lower band -> long, target basis.
    If require_close_back, wait for the bar to close back inside the band first."""
    C = df["Close"]; a = atr(df, atr_p)
    basis, ub, lb = bollinger(C, length, mult)
    adx_ = adx(df, adx_p) if adx_max > 0 else None
    cv, av, bv, uv, lv = C.values, a.values, basis.values, ub.values, lb.values
    adv = adx_.values if adx_ is not None else None
    pcv = C.shift(1).values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(bv[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        if adv is not None and (np.isnan(adv[i]) or adv[i] > adx_max):
            continue
        side_val = 0
        if require_close_back:
            if pcv[i] < lv[i] and cv[i] >= lv[i]:
                side_val = 1
            elif pcv[i] > uv[i] and cv[i] <= uv[i]:
                side_val = -1
        else:
            if cv[i] <= lv[i]:
                side_val = 1
            elif cv[i] >= uv[i]:
                side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(bv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr > 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def kc_revert(df, side="both", length=20, mult=2.0, atr_p=14, sl_atr=1.5,
              tp_frac=1.0, max_bars=24, limit_atr=0.0):
    """Keltner-channel extreme reversion (ATR bands around EMA basis)."""
    C = df["Close"]; a = atr(df, atr_p); basis = ema(C, length)
    ub = basis + mult * a; lb = basis - mult * a
    cv, av, bv, uv, lv = C.values, a.values, basis.values, ub.values, lb.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(bv[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        side_val = 1 if cv[i] <= lv[i] else (-1 if cv[i] >= uv[i] else 0)
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(bv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr > 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def rsi_snapback(df, side="both", rsi_p=14, lo=30, hi=70, sl_atr=1.5, tp_atr=1.5,
                 atr_p=14, max_bars=24, entry="market", limit_atr=0.0, cross_back=True):
    """RSI extreme snapback. cross_back: enter when RSI crosses back UP through lo
    (long) / back DOWN through hi (short). Else enter while RSI is beyond extreme."""
    C = df["Close"]; a = atr(df, atr_p); r = rsi(C, rsi_p)
    cv, av, rv, prv = C.values, a.values, r.values, r.shift(1).values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(rv[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        side_val = 0
        if cross_back:
            if prv[i] <= lo and rv[i] > lo:
                side_val = 1
            elif prv[i] >= hi and rv[i] < hi:
                side_val = -1
        else:
            if rv[i] <= lo:
                side_val = 1
            elif rv[i] >= hi:
                side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def stoch_snapback(df, side="both", k_len=14, smooth=3, lo=20, hi=80, sl_atr=1.5,
                   tp_atr=1.5, atr_p=14, max_bars=24, entry="market", limit_atr=0.0):
    """Stochastic %K cross back out of an extreme -> fade."""
    a = atr(df, atr_p); k = stoch_k(df, k_len, smooth); C = df["Close"]
    cv, av, kv, pkv = C.values, a.values, k.values, k.shift(1).values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(kv[i]) or np.isnan(av[i]) or av[i] <= 0:
            continue
        side_val = 0
        if pkv[i] <= lo and kv[i] > lo:
            side_val = 1
        elif pkv[i] >= hi and kv[i] < hi:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def vwap_revert(df, side="both", z_period=40, z_entry=2.0, sl_atr=1.5, tp_frac=0.7,
                atr_p=14, max_bars=24, limit_atr=0.0):
    """Fade extension from session VWAP. Distance-from-VWAP z-scored; beyond z_entry
    -> fade back toward VWAP (target tp_frac of the gap)."""
    C = df["Close"]; a = atr(df, atr_p); vwap = session_vwap(df)
    gap = C - vwap
    z = rolling_zscore(gap, z_period)
    cv, av, vv, zv = C.values, a.values, vwap.values, z.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(vv[i]):
            continue
        side_val = 1 if zv[i] <= -z_entry else (-1 if zv[i] >= z_entry else 0)
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(vv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr > 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def vwap_reclaim(df, side="both", sl_atr=1.5, tp_atr=2.0, atr_p=14, max_bars=24,
                 entry="market", limit_atr=0.0, buf_atr=0.0):
    """Follow a VWAP reclaim/loss. Close crosses back above VWAP (+buffer) -> long."""
    C = df["Close"]; a = atr(df, atr_p); vwap = session_vwap(df)
    cv, av, vv, pcv = C.values, a.values, vwap.values, C.shift(1).values
    pvv = vwap.shift(1).values
    sigs = []
    for i in range(1, len(df)):
        if np.isnan(vv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(pvv[i]):
            continue
        buf = buf_atr * av[i]
        side_val = 0
        if pcv[i] <= pvv[i] and cv[i] > vv[i] + buf:
            side_val = 1
        elif pcv[i] >= pvv[i] and cv[i] < vv[i] - buf:
            side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def wick_fade(df, side="both", wick_frac=0.6, min_range_atr=1.0, sl_atr=0.5,
              tp_atr=1.2, atr_p=14, max_bars=16, entry="market", limit_atr=0.0):
    """Single-bar rejection-wick fade (stop-run scalp). A bar with a long lower
    wick (>= wick_frac of its range) and a body in the upper part -> long; stop
    just beyond the wick low. No prior-swing requirement -> high frequency."""
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    ov, hv, lv, cv, av = O.values, H.values, L.values, C.values, a.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0:
            continue
        rng = hv[i] - lv[i]
        if rng <= 0 or rng < min_range_atr * av[i]:
            continue
        top = max(ov[i], cv[i]); bot = min(ov[i], cv[i])
        lower_wick = bot - lv[i]
        upper_wick = hv[i] - top
        side_val = 0
        sl_dist = sl_atr * av[i]
        if lower_wick >= wick_frac * rng and cv[i] > ov[i]:
            side_val = 1
            sl_dist = (cv[i] - lv[i]) + sl_atr * av[i]
        elif upper_wick >= wick_frac * rng and cv[i] < ov[i]:
            side_val = -1
            sl_dist = (hv[i] - cv[i]) + sl_atr * av[i]
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_dist, tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def atr_climax_fade(df, side="both", range_mult=2.0, atr_p=14, sl_atr=1.0,
                    tp_atr=1.5, max_bars=16, entry="market", limit_atr=0.0):
    """Volatility-climax fade. A bar whose range >= range_mult*ATR is a blow-off;
    fade the direction of the climax bar's close vs open."""
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    ov, hv, lv, cv, av = O.values, H.values, L.values, C.values, a.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(av[i]) or av[i] <= 0:
            continue
        rng = hv[i] - lv[i]
        if rng < range_mult * av[i]:
            continue
        side_val = -1 if cv[i] > ov[i] else 1  # fade the climax direction
        if not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def micro_pullback(df, side="both", impulse_atr=1.5, pull_bars=2, ema_len=20,
                   sl_atr=1.0, tp_atr=2.0, atr_p=14, max_bars=20, entry="market",
                   limit_atr=0.0):
    """Momentum continuation. A strong impulse bar (range>=impulse_atr*ATR closing
    in trend direction above/below EMA), then a shallow <=pull_bars pullback that
    doesn't break the impulse low/high -> continue."""
    O, H, L, C = df["Open"], df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p); e = ema(C, ema_len)
    ov, hv, lv, cv, av, ev = O.values, H.values, L.values, C.values, a.values, e.values
    n = len(df)
    sigs = []
    for i in range(pull_bars + 1, n):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(ev[i]):
            continue
        imp = i - pull_bars - 1  # impulse bar candidate
        rng = hv[imp] - lv[imp]
        if rng < impulse_atr * av[imp]:
            continue
        up_imp = cv[imp] > ov[imp] and cv[imp] > ev[imp]
        dn_imp = cv[imp] < ov[imp] and cv[imp] < ev[imp]
        side_val = 0
        if up_imp:
            # shallow pullback: subsequent bars stay above impulse low, current closes up
            if min(lv[imp + 1:i + 1]) > lv[imp] and cv[i] > cv[i - 1]:
                side_val = 1
        elif dn_imp:
            if max(hv[imp + 1:i + 1]) < hv[imp] and cv[i] < cv[i - 1]:
                side_val = -1
        if side_val == 0 or not _allow(side_val, side):
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_atr * av[i],
                           entry_style=entry, limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


# ---------------------------------------------------------------------------
def orb_fade(df, side="both", open_bars=12, sl_atr=1.0, tp_frac=0.7, atr_p=14,
             max_bars=24, limit_atr=0.0):
    """Opening-range fade. Define the day's first open_bars range; when price breaks
    it then closes back inside, fade back toward the range mid. One setup per side
    per day until filled. UTC day anchor."""
    H, L, C = df["High"], df["Low"], df["Close"]
    a = atr(df, atr_p)
    day = df.index.normalize()
    hv, lv, cv, av = H.values, L.values, C.values, a.values
    sigs = []
    # precompute per-day opening range
    or_hi = {}; or_lo = {}
    counts = {}
    days = day.values
    for i in range(len(df)):
        d = days[i]
        counts[d] = counts.get(d, 0) + 1
        k = counts[d]
        if k <= open_bars:
            or_hi[d] = max(or_hi.get(d, -1e18), hv[i])
            or_lo[d] = min(or_lo.get(d, 1e18), lv[i])
            continue
        if np.isnan(av[i]) or av[i] <= 0:
            continue
        rh, rl = or_hi.get(d), or_lo.get(d)
        if rh is None:
            continue
        mid = (rh + rl) / 2.0
        side_val = 0
        if hv[i] > rh and cv[i] < rh:        # broke above, closed back in -> fade short
            side_val = -1
        elif lv[i] < rl and cv[i] > rl:      # broke below, closed back in -> fade long
            side_val = 1
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(mid - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr > 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars))
    return sigs


def regime_mr(df, side="both", trend_len=200, slope_lb=20, z_period=30, z_entry=1.5,
              sl_atr=1.5, tp_frac=0.4, max_bars=12, limit_atr=0.25, atr_p=14,
              be_trigger_r=0.0, be_offset_r=0.0, tp1_frac=1.0, tp2_mult=0.0,
              be_after_tp1=False, accel_mult=0.0, min_slope_pct=0.0):
    """Regime-gated VWAP mean-reversion: fade extensions ONLY in the direction of
    the higher-timeframe trend (EMA(trend_len) slope). Buy dips back to session
    VWAP in an uptrend; sell rips in a downtrend. Maker limit entry. This is the
    'MTF regime filter + LTF trigger' family and the one that GENERALIZES across
    coins (vs plain vwap_revert which only worked on the trending coin).

    accel_mult>0 = acceleration guard: skip the fade when the TRIGGER bar's range
    is >= accel_mult*ATR (a volatility-climax / news-rip bar). Fading into an
    accelerating move is what turns the rare loss into a full 2-ATR stop — this
    declines to fade the blow-off bars. Default 0.0 = off (behavior preserved)."""
    O, H, L = df["Open"], df["High"], df["Low"]
    C = df["Close"]; a = atr(df, atr_p)
    e = ema(C, trend_len); slope = e - e.shift(slope_lb)
    vwap = session_vwap(df)
    z = rolling_zscore(C - vwap, z_period)
    cv, av, vv, zv, slv = C.values, a.values, vwap.values, z.values, slope.values
    hv, lv, ev = H.values, L.values, e.values
    sigs = []
    for i in range(len(df)):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(slv[i]) or np.isnan(vv[i]):
            continue
        if accel_mult > 0 and (hv[i] - lv[i]) >= accel_mult * av[i]:
            continue  # acceleration guard: don't fade a volatility-climax bar
        # trend-clarity gate: don't fade against a barely-sloped (ambiguous) trend
        # — the sign-only gate shorts a flat-but-rising tape and gets run over.
        if min_slope_pct > 0 and ev[i] and abs(slv[i] / ev[i] * 100.0) < min_slope_pct:
            continue
        up = slv[i] > 0
        side_val = 1 if (zv[i] <= -z_entry and up) else (-1 if (zv[i] >= z_entry and not up) else 0)
        if side_val == 0 or not _allow(side_val, side):
            continue
        exp_entry = cv[i] - limit_atr * av[i] if side_val > 0 else cv[i] + limit_atr * av[i]
        tp_dist = abs(vv[i] - exp_entry) * tp_frac
        if tp_dist <= 0:
            continue
        sigs.append(Signal(i=i, side=side_val, sl_dist=sl_atr * av[i], tp_dist=tp_dist,
                           entry_style="limit" if limit_atr > 0 else "market",
                           limit_dist=limit_atr * av[i], max_bars=max_bars,
                           be_trigger_r=be_trigger_r, be_offset_r=be_offset_r,
                           tp1_frac=tp1_frac, tp2_dist=tp2_mult * tp_dist,
                           be_after_tp1=be_after_tp1))
    return sigs


REGISTRY = {
    # scalping-specific (this file)
    "regime_mr": regime_mr,
    "bb_revert": bb_revert,
    "kc_revert": kc_revert,
    "rsi_snapback": rsi_snapback,
    "stoch_snapback": stoch_snapback,
    "vwap_revert": vwap_revert,
    "vwap_reclaim": vwap_reclaim,
    "wick_fade": wick_fade,
    "atr_climax_fade": atr_climax_fade,
    "micro_pullback": micro_pullback,
    "orb_fade": orb_fade,
    # prior six (reused)
    "range_fade": _prior.range_fade,
    "failed_breakout": _prior.failed_breakout,
    "sweep_reversal": _prior.sweep_reversal,
    "squeeze_expansion": _prior.squeeze_expansion,
    "reclaim_pullback": _prior.reclaim_pullback,
    "mr_fade2": _prior.mr_fade2,
}
