"""Coverage for Apex's kept PLAIN-retest entry gate (replaces test_reclaim_gap).

Exercises the two pure entry-gate functions on the live path:
  - check_retest:        EMA9 wick-touch (with bounded overshoot), long & short
  - passes_entry_filters: slope gate + body/ATR band block + weekday block
"""
import pandas as pd

from apex_bridge.signals import check_retest, passes_entry_filters


# ---------------- check_retest ----------------
# overshoot = ema * (overshoot_pct/100). With ema=100, overshoot_pct=0.2 -> 0.2,
# so a LONG retest needs bar_low in [99.8, 100.0]; a SHORT needs bar_high in
# [100.0, 100.2].

def test_check_retest_long_touches_ema():
    # wick dips exactly to EMA9 -> touch counts
    assert check_retest("long", 100.0, bar_low=100.0, bar_high=101.0, overshoot_pct=0.2) is True
    # wick pokes just below EMA9 within the 0.2% overshoot band
    assert check_retest("long", 100.0, bar_low=99.85, bar_high=101.0, overshoot_pct=0.2) is True


def test_check_retest_long_no_touch_or_too_deep():
    # never reached EMA9 (low stays above it) -> no retest
    assert check_retest("long", 100.0, bar_low=100.5, bar_high=101.0, overshoot_pct=0.2) is False
    # broke through past the overshoot band (knife-through) -> rejected
    assert check_retest("long", 100.0, bar_low=99.5, bar_high=101.0, overshoot_pct=0.2) is False


def test_check_retest_short_touches_ema():
    # wick rises exactly to EMA9
    assert check_retest("short", 100.0, bar_low=99.0, bar_high=100.0, overshoot_pct=0.2) is True
    # wick pokes just above EMA9 within the 0.2% overshoot band
    assert check_retest("short", 100.0, bar_low=99.0, bar_high=100.15, overshoot_pct=0.2) is True


def test_check_retest_short_no_touch_or_too_deep():
    # high stays below EMA9 -> never retested
    assert check_retest("short", 100.0, bar_low=99.0, bar_high=99.5, overshoot_pct=0.2) is False
    # blew past the overshoot band above EMA9 -> rejected
    assert check_retest("short", 100.0, bar_low=99.0, bar_high=100.5, overshoot_pct=0.2) is False


# ---------------- passes_entry_filters ----------------
# Apex's locked config: min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
# block_weekdays=[] (trades Sundays).

WEEKDAY_TS = pd.Timestamp("2026-06-23 12:00:00")  # a Tuesday


def test_passes_entry_filters_clean_signal_fires():
    # slope above gate, body outside the chop band -> fires
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=0.40, body_atr=0.60,
        block_weekdays=[], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is True


def test_passes_entry_filters_blocks_flat_slope():
    # |slope| below the 0.15 gate -> blocked even with a clean body
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=0.05, body_atr=0.60,
        block_weekdays=[], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is False
    # negative slope below the gate (abs value) -> still blocked
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=-0.10, body_atr=0.60,
        block_weekdays=[], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is False


def test_passes_entry_filters_blocks_inside_body_band():
    # body/ATR inside the [0.3, 0.5) chop band -> blocked despite a strong slope
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=0.40, body_atr=0.40,
        block_weekdays=[], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is False
    # band is half-open: the upper edge 0.5 is NOT blocked
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=0.40, body_atr=0.50,
        block_weekdays=[], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is True


def test_passes_entry_filters_blocks_configured_weekday():
    # Tuesday is weekday()==1; blocking it stops an otherwise-clean signal
    assert passes_entry_filters(
        WEEKDAY_TS, slope_pct=0.40, body_atr=0.60,
        block_weekdays=[1], min_abs_slope_pct=0.15, block_body_band=(0.3, 0.5),
    ) is False
