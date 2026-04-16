"""EMA (Exponential Moving Average) — pure functions, no side effects."""
from __future__ import annotations
from typing import Sequence


def compute_ema(closes: Sequence[float], period: int) -> float:
    """Compute EMA from a sequence of close prices (oldest first).

    Uses SMA of the first `period` values as the seed, then applies
    the standard EMA formula for remaining values.

    Returns the final (most recent) EMA value.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(closes) < period:
        raise ValueError(
            f"need at least {period} closes for EMA({period}), got {len(closes)}"
        )

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
    return ema


def compute_ema_series(closes: Sequence[float], period: int) -> list[float]:
    """Return one EMA value per closing bar starting at index `period - 1`.

    Length of returned list = len(closes) - period + 1.
    First value is the SMA seed; each subsequent value applies the EMA step.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(closes) < period:
        raise ValueError(
            f"need at least {period} closes for EMA({period}), got {len(closes)}"
        )

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    series = [ema]
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
        series.append(ema)
    return series


def compute_ema_slope(
    closes: Sequence[float], period: int, lookback: int = 1,
) -> float:
    """Slope of the EMA between the most recent value and `lookback` bars prior.

    Returned as raw price-per-bar delta (positive = rising). Caller can
    normalize to a percentage if desired.
    """
    if lookback < 1:
        raise ValueError(f"lookback must be >= 1, got {lookback}")
    series = compute_ema_series(closes, period)
    if len(series) < lookback + 1:
        raise ValueError(
            f"need at least {lookback + 1} EMA values for slope, got {len(series)}"
        )
    return series[-1] - series[-1 - lookback]
