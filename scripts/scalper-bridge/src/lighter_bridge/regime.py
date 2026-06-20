"""Regime-gated VWAP mean-reversion ("regime_mr") signal generator — Scalper.

EXACT port of the validated backtest `strat_lib.regime_mr`
(scripts/scalping/analysis/scalp_search_2026-05-30). Fade the z-score of
(Close - sessionVWAP) back toward VWAP, but ONLY in the direction of the
higher-timeframe trend (EMA(trend_len) slope):

    uptrend  (slope>0) + z <= -z_entry  -> LONG  (buy the dip)
    downtrend(slope<0) + z >= +z_entry  -> SHORT (sell the rip)

Entry is a maker LIMIT at close -/+ limit_atr*ATR (favorable). Exits: hard stop
sl_atr*ATR, take-profit at tp_frac * (entry distance to VWAP), time stop max_bars.

CRITICAL — parity: the backtest's btengine uses a PLAIN EWM EMA (span, adjust=False)
and Wilder-RMA ATR, and a daily-anchored session VWAP. Those exact formulas are
reproduced here (NOT indicators.py's Pine-seeded variants), so live signals match
the validated edge. `prepare_regime` is stateless — recomputed from the full df
each call, exactly like the backtest iterates the whole series.

`prepare_regime` adds columns: ema_trend, slope, vwap, zscore, atr14,
reg_long (bool), reg_short (bool). The bool flags are True on the just-closed bar
when it is a fresh signal bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# --- exact btengine indicator formulas (do NOT swap for indicators.py Pine versions) ---

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return _rma(tr, period)


def _rolling_zscore(series: pd.Series, period: int) -> pd.Series:
    m = series.rolling(period, min_periods=period).mean()
    s = series.rolling(period, min_periods=period).std(ddof=0)
    return (series - m) / s.replace(0.0, np.nan)


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, np.nan).ffill().fillna(1.0)
    day = df.index.normalize()
    pv = (tp * vol).groupby(day).cumsum()
    vv = vol.groupby(day).cumsum()
    return pv / vv


def prepare_regime(df: pd.DataFrame, trend_len: int = 200, slope_lb: int = 20,
                   z_period: int = 30, z_entry: float = 1.5,
                   atr_period: int = 14, accel_mult: float = 0.0,
                   min_slope_pct: float = 0.0) -> pd.DataFrame:
    """accel_mult>0 = acceleration guard: a signal bar whose range (High-Low) is
    >= accel_mult*ATR is a volatility-climax / news-rip bar; decline to fade it.
    Fading into an accelerating move is what turns the rare loss into a full
    sl_atr stop. Validated on the live 6-coin basket (fresh_basket_test.py):
    accel_mult=3.0 lifts pooled PF 1.46->1.54, lowers DD, helps 4/5 coins,
    ~1% fewer trades. Default 0.0 = OFF (exact prior behavior preserved).

    min_slope_pct>0 = trend-clarity gate: require |EMA-slope| (% of EMA over
    slope_lb bars) >= min_slope_pct before fading against the trend. The sign-only
    gate fires into a flat-but-rising tape (shorting a rally) and gets run over —
    the regime forensic on the -$926 window (chunk1_forensics.py) showed the
    losers had near-zero slope. Validated across 9 time-chunks (slope_gate_test.py):
    0.08 nearly eliminates the worst losing window (-$926 -> -$126), LIFTS total
    net +20%, and the good windows stay above baseline — NOT conservatism, it
    drops only net-losing ambiguous-trend fades. Default 0.0 = OFF."""
    out = df.copy()
    c = out["Close"].astype(float)
    e = _ema(c, trend_len)
    slope = e - e.shift(slope_lb)
    vwap = _session_vwap(out)
    z = _rolling_zscore(c - vwap, z_period)
    a = _atr(out, atr_period)

    cv, av, vv, zv, slv = c.values, a.values, vwap.values, z.values, slope.values
    hv, lv, ev = out["High"].values, out["Low"].values, e.values
    n = len(out)
    reg_long = np.zeros(n, dtype=bool)
    reg_short = np.zeros(n, dtype=bool)
    for i in range(n):
        if np.isnan(zv[i]) or np.isnan(av[i]) or av[i] <= 0 or np.isnan(slv[i]) or np.isnan(vv[i]):
            continue
        if accel_mult > 0 and (hv[i] - lv[i]) >= accel_mult * av[i]:
            continue  # acceleration guard: don't fade a volatility-climax bar
        if min_slope_pct > 0 and ev[i] and abs(slv[i] / ev[i] * 100.0) < min_slope_pct:
            continue  # trend-clarity gate: don't fade a flat/ambiguous trend
        up = slv[i] > 0
        if zv[i] <= -z_entry and up:
            reg_long[i] = True
        elif zv[i] >= z_entry and not up:
            reg_short[i] = True

    out["ema_trend"] = e
    out["slope"] = slope
    out["vwap"] = vwap
    out["zscore"] = z
    out["atr14"] = a
    out["reg_long"] = reg_long
    out["reg_short"] = reg_short
    return out


def entry_levels(side: str, close: float, atr: float, vwap: float,
                 limit_atr: float, sl_atr: float, tp_frac: float) -> dict:
    """Compute the maker limit price + SL/TP distances at signal time (matches
    btengine: distances fixed off the SIGNAL bar's close/atr/vwap)."""
    if side == "long":
        limit_px = close - limit_atr * atr
    else:
        limit_px = close + limit_atr * atr
    sl_dist = sl_atr * atr
    tp_dist = abs(vwap - limit_px) * tp_frac
    return {"limit_px": limit_px, "sl_dist": sl_dist, "tp_dist": tp_dist}
