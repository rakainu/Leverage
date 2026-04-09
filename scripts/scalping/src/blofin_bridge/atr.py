"""Wilder's ATR (Average True Range) — pure function, no side effects.

Used by the entry handler to compute dynamic SL/TP distances that mirror
what SMRT Algo Pro V3 paints on the chart.
"""
from __future__ import annotations
from typing import Sequence


class ATRError(ValueError):
    """Raised when ATR cannot be computed (insufficient data, bad length, etc)."""


# OHLCV bar shape: [timestamp, open, high, low, close, volume]
# Indices for clarity
_O, _H, _L, _C = 1, 2, 3, 4


def _true_range(prev_close: float, high: float, low: float) -> float:
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def wilders_atr(bars: Sequence[Sequence[float]], length: int) -> float:
    """Return the final Wilder's ATR value for the given bars.

    Requires at least `length + 1` bars so the first `length` True Ranges can be
    computed against a previous close. Returns the last smoothed ATR value.

    Args:
        bars: sequence of OHLCV bars [[ts, o, h, l, c, v], ...] in chronological order.
        length: ATR period (e.g. 14).
    """
    if length <= 0:
        raise ATRError(f"length must be positive, got {length}")
    if len(bars) < length + 1:
        raise ATRError(
            f"need at least {length + 1} bars for ATR({length}), got {len(bars)}"
        )

    # Compute True Ranges starting from bar 1 (using bar 0's close as prev).
    trs: list[float] = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1][_C]
        high = bars[i][_H]
        low = bars[i][_L]
        trs.append(_true_range(prev_close, high, low))

    # Wilder's initialization: SMA of the first `length` TRs.
    if len(trs) < length:
        raise ATRError(
            f"after TR computation, got {len(trs)} TRs, need {length}"
        )
    atr = sum(trs[:length]) / length

    # Smooth forward with the remaining TRs.
    for tr in trs[length:]:
        atr = ((atr * (length - 1)) + tr) / length

    return atr
