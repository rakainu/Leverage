"""Squeeze (volatility compression -> expansion) signal generator.

EXACT port of the validated backtest `strat_lib.squeeze_expansion`
(scripts/scalping/analysis/lighter_strat_2026-05-30). Bollinger(bb_len, bb_mult)
inside Keltner(bb_len, kc_mult*ATR) = compression. On the FIRST non-squeezed bar
after >= min_squeeze consecutive squeezed bars (a "release"), fire in the
direction of momentum (close vs basis SMA).

`prepare_squeeze` returns the df with added columns:
  basis, atr14, sq_long (bool), sq_short (bool)
sq_long / sq_short are True ONLY on a release bar — i.e. the just-closed bar.

Stateless: recomputed from the full df each call (matches the backtest, which
iterates the whole series). No persistent run-length state to drift.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import calc_sma, calc_stdev, calc_atr


def prepare_squeeze(df: pd.DataFrame, bb_len: int = 20, bb_mult: float = 2.0,
                    kc_mult: float = 1.5, min_squeeze: int = 10,
                    atr_period: int = 14) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"].astype(float)
    basis = calc_sma(c, bb_len)
    dev = calc_stdev(c, bb_len)
    a = calc_atr(out, atr_period)
    upper_bb = basis + bb_mult * dev
    lower_bb = basis - bb_mult * dev
    upper_kc = basis + kc_mult * a
    lower_kc = basis - kc_mult * a
    squeeze_on = (upper_bb < upper_kc) & (lower_bb > lower_kc)
    mom = c - basis

    cv, av, bv, mvv = c.values, a.values, basis.values, mom.values
    sqv = squeeze_on.values
    n = len(out)
    sq_long = np.zeros(n, dtype=bool)
    sq_short = np.zeros(n, dtype=bool)
    run = 0
    for i in range(n):
        if np.isnan(av[i]) or av[i] <= 0 or np.isnan(bv[i]):
            run = 0
            continue
        if sqv[i]:
            run += 1
            continue
        # squeeze just released this bar after >= min_squeeze squeezed bars
        if run >= min_squeeze:
            if mvv[i] > 0:
                sq_long[i] = True
            else:
                sq_short[i] = True
        run = 0

    out["basis"] = basis
    out["atr14"] = a
    out["sq_long"] = sq_long
    out["sq_short"] = sq_short
    return out
