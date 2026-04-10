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
