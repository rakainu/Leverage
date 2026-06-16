"""Rebound — 1H VWAP-anchored range mean-reversion FADE signal generator.

EXACT port of the validated backtest (scripts/scalping/analysis/donchian_breakout_2026-06-15
/mr_engine.py, champion config). In a RANGE regime (ADX < adx_max — never in a trend),
when price pokes beyond the VWAP-anchored band (bb_mult * std of close-from-VWAP) and
then CLOSES back inside, fade it toward VWAP:

    poked below lower band last bar, closed back inside this bar, below VWAP -> LONG
    poked above upper band last bar, closed back inside this bar, above VWAP -> SHORT

Exit (handled by exit_model='rebound' in main): bank tp1_frac at the VWAP mean, trail
the runner at atr_trail*ATR, hard stop atr_stop*ATR (capped per coin), time stop max_bars.

CRITICAL — parity: uses the SAME formulas as mr_engine (rolling VWAP over vwap_len,
band dev = std(close - basis, ddof=0), Wilder-RMA ATR, EWM-smoothed ADX). Stateless:
recomputed from the full df each call, exactly like the backtest iterates the series.

prepare_rebound adds columns: basis (VWAP), upper, lower, zscore, adx, atr14, atr_pct,
reb_long (bool), reb_short (bool). The bool flags are True on the just-closed bar when
it is a fresh reclaim signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --- exact backtest (mr_engine) indicator formulas — do NOT swap for Pine variants ---

def _rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return _rma(tr, period)


def _rolling_vwap(df: pd.DataFrame, length: int) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    pv = (tp * df["Volume"]).rolling(length, min_periods=length).sum()
    vv = df["Volume"].rolling(length, min_periods=length).sum()
    return pv / vv.replace(0, np.nan)


def _adx(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    up = h.diff(); dn = -l.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * plus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def prepare_rebound(df: pd.DataFrame, vwap_len: int = 48, bb_len: int = 20,
                    bb_mult: float = 2.5, adx_len: int = 14, adx_max: float = 20.0,
                    atr_period: int = 14, atr_min_pct: float = 0.4) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"].astype(float)
    basis = _rolling_vwap(out, vwap_len)
    dev = (c - basis).rolling(bb_len, min_periods=bb_len).std(ddof=0)
    upper = basis + bb_mult * dev
    lower = basis - bb_mult * dev
    z = (c - basis) / dev.replace(0, np.nan)
    a = _atr(out, atr_period)
    atr_pct = a / c * 100.0
    adx = _adx(out, adx_len)

    cv = c.values
    c_prev = c.shift(1).values
    lo_v, up_v, ba_v = lower.values, upper.values, basis.values
    lo_prev, up_prev = lower.shift(1).values, upper.shift(1).values
    av, apv, adv = a.values, atr_pct.values, adx.values
    n = len(out)
    reb_long = np.zeros(n, dtype=bool)
    reb_short = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(ba_v[i]):
            continue
        if not np.isfinite(apv[i]) or apv[i] <= atr_min_pct:
            continue
        if adx_max > 0 and (np.isnan(adv[i]) or adv[i] > adx_max):
            continue  # only fade in a range, never in a trend
        if np.isnan(lo_prev[i]) or np.isnan(c_prev[i]):
            continue
        # poke below lower band last bar, close back inside this bar, still below VWAP
        if (c_prev[i] < lo_prev[i]) and (cv[i] >= lo_v[i]) and (cv[i] < ba_v[i]):
            reb_long[i] = True
        elif (c_prev[i] > up_prev[i]) and (cv[i] <= up_v[i]) and (cv[i] > ba_v[i]):
            reb_short[i] = True

    out["basis"] = basis
    out["upper"] = upper
    out["lower"] = lower
    out["zscore"] = z
    out["adx"] = adx
    out["atr14"] = a
    out["atr_pct"] = atr_pct
    out["reb_long"] = reb_long
    out["reb_short"] = reb_short
    return out


def entry_levels(side: str, close: float, atr: float, basis: float,
                 atr_stop: float, stop_cap_frac: float) -> dict:
    """Stop = atr_stop*ATR capped at stop_cap_frac of price; TP = the VWAP mean (basis)
    at signal time. Matches mr_engine: distances/targets fixed off the SIGNAL bar."""
    want = atr_stop * atr
    cap = stop_cap_frac * close
    skip = want > cap            # mr_engine: skip the trade rather than tighten
    sl_dist = min(want, cap)
    tp_price = basis             # fade target = reversion to VWAP
    return {"sl_dist": sl_dist, "tp_price": tp_price, "skip": skip}
