"""Z-Fade signal generation. Mirrors sweeps/2026-05-20/strat_zscore.py EXACTLY.

Z-score mean-reversion fade:
  long  = z < -z_thresh  [+ RSI<os, BB-width>min, (EMA off), ADX<=adx_max regime]
  short = z > +z_thresh   [+ RSI>ob, ...]
Market entry on the just-closed bar; ATR-based exits live in state_machine.py.

Backtest/live parity is the whole point — keep this in lockstep with strat_zscore.py.
"""
from __future__ import annotations

import pandas as pd

from .config import ZFadeConfig
from .indicators import calc_ema, calc_atr, calc_rsi, calc_adx, calc_zscore


def prepare(df: pd.DataFrame, cfg: ZFadeConfig) -> pd.DataFrame:
    """Enrich a 5m OHLCV DataFrame with all Z-Fade indicator columns."""
    df = df.copy()
    c = df["Close"]
    df["zscore"] = calc_zscore(c, cfg.window).values
    df["rsi"] = calc_rsi(c, cfg.rsi_len).values
    bb_basis = c.rolling(cfg.bb_len).mean()
    bb_std = c.rolling(cfg.bb_len).std(ddof=0)               # BB default = population
    df["bb_width"] = ((2 * cfg.bb_mult * bb_std) / bb_basis).values
    df["ema"] = calc_ema(c, cfg.ema_len).values
    df["adx"] = calc_adx(df, cfg.adx_len).values
    df["atr"] = calc_atr(df, cfg.atr_len).values
    return df


def evaluate_entry(row: pd.Series, cfg: ZFadeConfig) -> str | None:
    """Return 'long' / 'short' / None for the latest enriched bar.

    Mirrors strat_zscore.run()'s entry gate: BB-width volatility floor, ADX
    regime gate, z-score extreme, optional RSI confirmation and EMA trend filter.
    """
    z = row["zscore"]; rsi = row["rsi"]; bbw = row["bb_width"]
    ema = row["ema"]; adx = row["adx"]; close = row["Close"]
    if any(pd.isna(x) for x in (z, rsi, bbw, ema, adx, row["atr"])):
        return None

    bb_ok = (not cfg.use_bb) or bbw > cfg.bb_width_min
    regime_ok = (not cfg.use_adx) or adx <= cfg.adx_max
    if not (bb_ok and regime_ok):
        return None

    rsi_lo = (not cfg.use_rsi) or rsi < cfg.rsi_os
    rsi_hi = (not cfg.use_rsi) or rsi > cfg.rsi_ob
    ema_lo = (not cfg.use_ema) or close > ema
    ema_hi = (not cfg.use_ema) or close < ema

    if z < -cfg.z_thresh and rsi_lo and ema_lo:
        return "long"
    if z > cfg.z_thresh and rsi_hi and ema_hi:
        return "short"
    return None
