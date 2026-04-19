"""Tests for signal_validator — pure invalidation/retest/revalidation logic."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone, timedelta

import pytest

from blofin_bridge.signal_validator import (
    SignalSnapshot,
    ValidationConfig,
    MarketContext,
    check_invalidation,
    check_retest,
    check_revalidation,
)


# ----------------------- fixtures -----------------------

def _snap(**over):
    """Default long snapshot at price=100, EMA=100, slope=+0.1, signal bar high/low 101/99."""
    base = dict(
        symbol="SOL-USDT", action="buy",
        signal_price=100.0, signal_candle_high=101.0, signal_candle_low=99.0,
        signal_ema_value=100.0, signal_ema_slope=0.1, signal_atr=1.0,
        signal_bar_ts=1_700_000_000_000, signal_timeframe="5m",
        received_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc),
        max_age_seconds=900, max_bars=3,
    )
    base.update(over)
    return SignalSnapshot(**base)


def _cfg(**over):
    base = dict(
        ema_length=9,
        max_signal_age_seconds=900,
        max_signal_bars=3,
        max_price_drift_percent=0.35,
        require_retest_confirmation_candle=True,
        cancel_on_slope_flip=True,
        bar_seconds=300,
        ema_retest_max_overshoot_pct=0.2,
    )
    base.update(over)
    return ValidationConfig(**base)


def _ctx(**over):
    """Default market context: price=100, EMA=100, slope=+0.1, last bar close=100."""
    base = dict(
        now=datetime(2026, 4, 16, 12, 5, 0, tzinfo=timezone.utc),
        last_price=100.0,
        current_ema=100.0,
        current_ema_slope=0.1,
        latest_bar_ts=1_700_000_000_000,
        last_closed_bar_close=100.0,
        # Bars closed since signal (closes only) — used for structure break scan
        closes_since_signal=[100.0],
        position_open=False,
    )
    base.update(over)
    return MarketContext(**base)


# ----------------------- invalidation: position already open -----------------------

def test_invalidated_when_position_open():
    assert check_invalidation(_snap(), _ctx(position_open=True), _cfg()) \
        == "invalidated_position_open"


# ----------------------- invalidation: time limit -----------------------

def test_expired_time_limit():
    snap = _snap(received_at=datetime(2026, 4, 16, 11, 0, 0, tzinfo=timezone.utc))
    ctx = _ctx(now=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc))  # 60m later
    assert check_invalidation(snap, ctx, _cfg(max_signal_age_seconds=900)) \
        == "expired_time_limit"


def test_time_limit_not_exceeded():
    snap = _snap(received_at=datetime(2026, 4, 16, 12, 0, 0, tzinfo=timezone.utc))
    ctx = _ctx(now=datetime(2026, 4, 16, 12, 5, 0, tzinfo=timezone.utc))  # 5m
    assert check_invalidation(snap, ctx, _cfg(max_signal_age_seconds=900)) is None


# ----------------------- invalidation: bar limit -----------------------

def test_expired_bar_limit():
    # Each bar = 300s (5m). 4 bars elapsed should fail max_bars=3.
    snap = _snap(signal_bar_ts=1_700_000_000_000)
    ctx = _ctx(latest_bar_ts=1_700_000_000_000 + 4 * 300_000)
    assert check_invalidation(snap, ctx, _cfg(max_signal_bars=3, bar_seconds=300)) \
        == "expired_bar_limit"


def test_bar_limit_not_exceeded():
    snap = _snap(signal_bar_ts=1_700_000_000_000)
    ctx = _ctx(latest_bar_ts=1_700_000_000_000 + 2 * 300_000)  # 2 bars
    assert check_invalidation(snap, ctx, _cfg(max_signal_bars=3, bar_seconds=300)) is None


# ----------------------- invalidation: structure break -----------------------

def test_long_structure_break_when_close_below_signal_low():
    snap = _snap(action="buy", signal_candle_low=99.0)
    ctx = _ctx(closes_since_signal=[100.0, 98.5, 99.5])
    assert check_invalidation(snap, ctx, _cfg()) == "invalidated_structure_break"


def test_long_structure_intact_when_closes_above_low():
    snap = _snap(action="buy", signal_candle_low=99.0)
    ctx = _ctx(closes_since_signal=[100.0, 99.5, 100.5])
    assert check_invalidation(snap, ctx, _cfg()) is None


def test_short_structure_break_when_close_above_signal_high():
    snap = _snap(action="sell", signal_ema_slope=-0.1, signal_candle_high=101.0)
    ctx = _ctx(current_ema_slope=-0.1, closes_since_signal=[100.0, 101.5])
    assert check_invalidation(snap, ctx, _cfg()) == "invalidated_structure_break"


# ----------------------- invalidation: slope flip -----------------------

def test_long_slope_flip_when_enabled():
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05)  # flipped
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) \
        == "invalidated_slope_flip"


def test_long_slope_flip_ignored_when_disabled():
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05)
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=False)) is None


def test_short_slope_flip():
    snap = _snap(action="sell", signal_ema_slope=-0.1)
    ctx = _ctx(current_ema_slope=0.05, closes_since_signal=[100.0])
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) \
        == "invalidated_slope_flip"


def test_peak_sell_not_invalidated_while_ema_still_rising():
    """Pro V3 SELL signals typically fire at a peak — when the EMA is STILL
    rising (no flip has occurred yet). The invalidation-phase slope check must
    not kill these setups just because the absolute slope is still positive.
    Invalidate only on a REAL flip (slope was in our favor, now against)."""
    snap = _snap(action="sell", signal_ema_slope=0.8)  # still rising at signal
    ctx = _ctx(current_ema_slope=0.6, closes_since_signal=[100.0])
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) is None


def test_trough_buy_not_invalidated_while_ema_still_falling():
    """Mirror: BUY signals fired at a trough while EMA is still declining
    must not instantly invalidate during pending phase."""
    snap = _snap(action="buy", signal_ema_slope=-0.8)  # still falling at signal
    ctx = _ctx(current_ema_slope=-0.6)
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) is None


def test_slope_flip_persistence_first_tick_does_not_kill_when_threshold_is_2():
    """With slope_flip_required_consecutive=2, a single-tick flip (prior count
    0 + this tick 1) must NOT kill the signal. Protects retest pullbacks."""
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05, prior_slope_flip_count=0)
    assert check_invalidation(
        snap, ctx, _cfg(cancel_on_slope_flip=True,
                        slope_flip_required_consecutive=2),
    ) is None


def test_slope_flip_persistence_second_tick_kills_when_threshold_is_2():
    """With threshold=2 and a prior flip observation, this tick's flip pushes
    the count to 2 and invalidates."""
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05, prior_slope_flip_count=1)
    assert check_invalidation(
        snap, ctx, _cfg(cancel_on_slope_flip=True,
                        slope_flip_required_consecutive=2),
    ) == "invalidated_slope_flip"


def test_slope_flip_default_threshold_is_1_backwards_compatible():
    """Default slope_flip_required_consecutive=1 preserves legacy behavior:
    a single-tick flip kills the signal immediately."""
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05, prior_slope_flip_count=0)
    # _cfg() defaults omit slope_flip_required_consecutive → 1
    assert check_invalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) \
        == "invalidated_slope_flip"


def test_is_slope_flipped_against_helper():
    """Helper mirrors the invalidation branch exactly and is what the poller
    calls to drive its per-signal counter."""
    from blofin_bridge.signal_validator import is_slope_flipped_against
    # long: rising at signal, falling now → flipped
    assert is_slope_flipped_against(
        _snap(action="buy", signal_ema_slope=0.1),
        _ctx(current_ema_slope=-0.05),
    ) is True
    # long: rising at signal, still rising now → not flipped
    assert is_slope_flipped_against(
        _snap(action="buy", signal_ema_slope=0.1),
        _ctx(current_ema_slope=0.05),
    ) is False
    # long: falling at signal (trough-buy) → never "flipped" this direction
    assert is_slope_flipped_against(
        _snap(action="buy", signal_ema_slope=-0.1),
        _ctx(current_ema_slope=-0.2),
    ) is False
    # short: falling at signal, rising now → flipped
    assert is_slope_flipped_against(
        _snap(action="sell", signal_ema_slope=-0.1),
        _ctx(current_ema_slope=0.05),
    ) is True


# ----------------------- invalidation: price drift (directional) -----------------------
#
# Drift fires when price has moved PAST the EMA in the thesis direction by
# more than threshold — "we missed the entry window, price is running." It
# does NOT fire when price moves away from the EMA (thesis not yet developing)
# because retest just won't trigger and the bar/time limit handles staleness.

def test_long_drift_fires_when_price_passed_above_ema():
    """Long: signal below EMA, price later rallies through EMA and beyond
    — the retest opportunity is gone."""
    snap = _snap(action="buy", signal_price=100.0)
    # EMA=100, price=100.5 → passed EMA by 0.5% > 0.35% threshold
    ctx = _ctx(last_price=100.5, current_ema=100.0)
    assert check_invalidation(snap, ctx, _cfg(max_price_drift_percent=0.35)) \
        == "invalidated_price_drift"


def test_long_drift_does_not_fire_when_price_at_or_below_ema():
    """Long: price still at or below EMA (retest hasn't happened or is in
    progress) must NOT invalidate on drift."""
    snap = _snap(action="buy", signal_price=100.0)
    # Price dropped below signal and further from EMA (thesis not developing)
    # — old symmetric check killed this; new directional check does not.
    ctx = _ctx(last_price=99.0, current_ema=100.0)
    assert check_invalidation(snap, ctx, _cfg(max_price_drift_percent=0.35)) is None


def test_long_drift_at_retest_not_invalidated():
    """Price exactly at EMA (retest point) must not drift-invalidate."""
    snap = _snap(action="buy", signal_price=100.0)
    ctx = _ctx(last_price=100.0, current_ema=100.0)
    assert check_invalidation(snap, ctx, _cfg(max_price_drift_percent=0.35)) is None


def test_short_drift_fires_when_price_passed_below_ema():
    """Short: signal above EMA, price later drops through EMA and beyond —
    entry missed."""
    snap = _snap(action="sell", signal_price=100.0, signal_ema_slope=-0.1)
    # EMA=100, price=99.5 → passed below EMA by 0.5% > 0.35%
    ctx = _ctx(last_price=99.5, current_ema=100.0, current_ema_slope=-0.1)
    assert check_invalidation(snap, ctx, _cfg(max_price_drift_percent=0.35)) \
        == "invalidated_price_drift"


def test_short_drift_does_not_fire_when_price_rises_away_from_ema():
    """Short: price moved UP away from EMA (wrong way for thesis). Retest
    won't trigger; we just wait. Must not drift-invalidate."""
    snap = _snap(action="sell", signal_price=100.0, signal_ema_slope=-0.1)
    ctx = _ctx(last_price=101.0, current_ema=100.0,
               current_ema_slope=-0.1, closes_since_signal=[100.0])
    assert check_invalidation(snap, ctx, _cfg(max_price_drift_percent=0.35)) is None


# ----------------------- retest detection -----------------------

def test_long_retest_true_when_price_touches_ema_from_above():
    snap = _snap(action="buy")
    ctx = _ctx(last_price=100.0, current_ema=100.0)
    assert check_retest(snap, ctx, _cfg()) is True


def test_long_retest_false_when_price_above_ema():
    snap = _snap(action="buy")
    ctx = _ctx(last_price=102.0, current_ema=100.0)
    assert check_retest(snap, ctx, _cfg()) is False


def test_long_retest_false_when_overshot_below():
    # EMA=100, overshoot_pct=0.2% → max 0.2 below = 99.8. Price 99.5 blows through.
    snap = _snap(action="buy")
    ctx = _ctx(last_price=99.5, current_ema=100.0)
    assert check_retest(snap, ctx, _cfg(ema_retest_max_overshoot_pct=0.2)) is False


def test_short_retest_true_when_price_touches_ema_from_below():
    snap = _snap(action="sell", signal_ema_slope=-0.1)
    ctx = _ctx(last_price=100.0, current_ema=100.0, current_ema_slope=-0.1)
    assert check_retest(snap, ctx, _cfg()) is True


def test_short_retest_false_when_price_below_ema():
    snap = _snap(action="sell", signal_ema_slope=-0.1)
    ctx = _ctx(last_price=98.0, current_ema=100.0, current_ema_slope=-0.1)
    assert check_retest(snap, ctx, _cfg()) is False


# ----------------------- revalidation at retest -----------------------

def test_revalidation_passes_when_setup_still_valid():
    snap = _snap()
    # Long: close back above EMA = bullish rejection
    ctx = _ctx(last_price=100.0, current_ema=100.0,
               last_closed_bar_close=100.5)
    assert check_revalidation(snap, ctx, _cfg()) is None


def test_revalidation_fails_on_slope_flip_at_retest():
    snap = _snap(action="buy", signal_ema_slope=0.1)
    ctx = _ctx(current_ema_slope=-0.05,
               last_closed_bar_close=100.5)
    assert check_revalidation(snap, ctx, _cfg(cancel_on_slope_flip=True)) \
        == "retest_failed_slope"


def test_revalidation_fails_on_structure_break():
    snap = _snap(action="buy", signal_candle_low=99.0)
    ctx = _ctx(closes_since_signal=[100.0, 98.5])
    assert check_revalidation(snap, ctx, _cfg()) == "retest_failed_structure"


def test_long_revalidation_confirmation_candle_fails_when_close_below_ema():
    snap = _snap(action="buy")
    # Required confirmation: last closed bar close > current_ema. Here it's below.
    ctx = _ctx(current_ema=100.0, last_closed_bar_close=99.5)
    assert check_revalidation(
        snap, ctx, _cfg(require_retest_confirmation_candle=True),
    ) == "retest_failed_confirmation"


def test_short_revalidation_confirmation_candle_fails_when_close_above_ema():
    snap = _snap(action="sell", signal_ema_slope=-0.1)
    ctx = _ctx(current_ema=100.0, current_ema_slope=-0.1,
               last_closed_bar_close=100.5,
               closes_since_signal=[100.0])
    assert check_revalidation(
        snap, ctx, _cfg(require_retest_confirmation_candle=True),
    ) == "retest_failed_confirmation"


def test_revalidation_confirmation_disabled_passes_without_confirmation():
    snap = _snap(action="buy")
    ctx = _ctx(current_ema=100.0, last_closed_bar_close=99.5)
    assert check_revalidation(
        snap, ctx, _cfg(require_retest_confirmation_candle=False),
    ) is None
