"""Tests for Wilder ATR helper."""
import pytest

from blofin_bridge.atr import compute_atr


# Each bar: [ts, open, high, low, close, volume] — matches ccxt OHLCV shape.

def _bar(high: float, low: float, close: float, ts: int = 0) -> list[float]:
    return [ts, close, high, low, close, 100.0]


def test_atr_constant_bars_equals_range():
    """ATR of flat bars (same H and L every bar) equals the range H-L."""
    bars = [_bar(101.0, 99.0, 100.0) for _ in range(20)]
    atr = compute_atr(bars, period=14)
    assert atr == pytest.approx(2.0)


def test_atr_rejects_insufficient_bars():
    bars = [_bar(101.0, 99.0, 100.0) for _ in range(5)]
    with pytest.raises(ValueError, match="need at least"):
        compute_atr(bars, period=14)


def test_atr_positive_on_varied_range():
    """ATR of non-trivial bars is strictly positive."""
    bars = [
        _bar(100 + i * 0.5, 99 - i * 0.3, 99.5 + i * 0.1) for i in range(20)
    ]
    atr = compute_atr(bars, period=14)
    assert atr > 0
