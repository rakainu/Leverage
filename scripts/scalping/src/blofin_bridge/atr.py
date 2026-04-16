"""Wilder's ATR (Average True Range) — pure function, no side effects.

Each bar is the ccxt OHLCV shape: [timestamp, open, high, low, close, volume].
"""
from __future__ import annotations
from typing import Sequence


def compute_atr(bars: Sequence[Sequence[float]], period: int = 14) -> float:
    """Compute Wilder ATR over the most recent `period` bars.

    True Range for bar i (i >= 1) is:
        max(high - low, |high - prev_close|, |low - prev_close|)
    For bar 0 we use (high - low).

    Seeds with the simple mean of the first `period` TR values, then applies
    Wilder smoothing: ATR_i = (ATR_{i-1} * (period - 1) + TR_i) / period.

    Returns the final (most recent) ATR value.
    """
    if period <= 0:
        raise ValueError(f"period must be positive, got {period}")
    if len(bars) < period + 1:
        raise ValueError(
            f"need at least {period + 1} bars for ATR({period}), got {len(bars)}"
        )

    # Build true range series
    tr_series: list[float] = []
    prev_close: float | None = None
    for bar in bars:
        high = float(bar[2])
        low = float(bar[3])
        close = float(bar[4])
        if prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
        tr_series.append(tr)
        prev_close = close

    # Drop the first TR (it's H-L only, not a real true range) so that our
    # seed uses `period` consecutive "real" TR values.
    tr_series = tr_series[1:]
    if len(tr_series) < period:
        raise ValueError(
            f"need at least {period + 1} bars for ATR({period}), "
            f"got {len(bars)} (true-range count={len(tr_series)})"
        )

    # Wilder seed + smoothing
    atr = sum(tr_series[:period]) / period
    for tr in tr_series[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr
