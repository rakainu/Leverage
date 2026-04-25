"""EMA (Exponential Moving Average) — pure function, no side effects."""
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
    """Compute the full EMA series, one value per close from index `period-1` on.

    Result aligns so that out[i] corresponds to closes[period - 1 + i].
    Used by the slope gate, which compares the latest EMA value against
    the EMA value 3 bars earlier.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(closes) < period:
        raise ValueError(
            f"need at least {period} closes for EMA({period}), got {len(closes)}"
        )

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed
    series = [ema]
    for close in closes[period:]:
        ema = (close - ema) * multiplier + ema
        series.append(ema)
    return series
