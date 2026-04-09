import pytest
from blofin_bridge.atr import wilders_atr, ATRError


def _bar(ts, o, h, l, c, v=0.0):
    return [ts, o, h, l, c, v]


def test_wilders_atr_known_values():
    """Hand-computed ATR(3) from a simple 5-bar fixture.

    Bars (h, l, c):
      bar0:  10, 8,  9
      bar1:  11, 9,  10   TR = max(11-9, |11-9|, |9-9|) = 2
      bar2:  12, 10, 11   TR = max(12-10, |12-10|, |10-10|) = 2
      bar3:  13, 10, 12   TR = max(13-10, |13-11|, |10-11|) = 3
      bar4:  14, 11, 13   TR = max(14-11, |14-12|, |11-12|) = 3

    ATR(3) using the first 3 TRs (bars 1..3) as initial SMA:
      initial_atr = (2 + 2 + 3) / 3 = 2.333...
    Then smooth forward with bar4's TR = 3:
      atr = (2.333 * (3-1) + 3) / 3 = (4.667 + 3) / 3 = 2.556
    """
    bars = [
        _bar(1, 9, 10, 8, 9),
        _bar(2, 9, 11, 9, 10),
        _bar(3, 10, 12, 10, 11),
        _bar(4, 11, 13, 10, 12),
        _bar(5, 12, 14, 11, 13),
    ]
    atr = wilders_atr(bars, length=3)
    assert atr == pytest.approx(2.5555555, rel=1e-4)


def test_atr_needs_enough_bars():
    bars = [_bar(i, 1, 2, 0, 1) for i in range(3)]
    with pytest.raises(ATRError, match="need at least"):
        wilders_atr(bars, length=14)


def test_atr_rejects_invalid_length():
    bars = [_bar(i, 1, 2, 0, 1) for i in range(20)]
    with pytest.raises(ATRError, match="length"):
        wilders_atr(bars, length=0)
    with pytest.raises(ATRError, match="length"):
        wilders_atr(bars, length=-5)


def test_atr_with_flat_bars_is_zero():
    """If every bar is flat (h == l), ATR is 0."""
    bars = [_bar(i, 5, 5, 5, 5) for i in range(20)]
    atr = wilders_atr(bars, length=14)
    assert atr == 0.0


def test_atr_14_synthetic_gap():
    """Sanity check: ATR(14) over 15 bars with a big gap produces a positive value."""
    bars = []
    for i in range(15):
        # Bar with range 1.0 each
        bars.append(_bar(i, 100 + i, 101 + i, 100 + i, 100.5 + i))
    # Inject a gap on the last bar: prev close ~114.5, new open 120, range 2
    bars[-1] = _bar(15, 120, 122, 119, 121)
    atr = wilders_atr(bars, length=14)
    assert atr > 1.0  # gap pushed it above the baseline of 1.0
