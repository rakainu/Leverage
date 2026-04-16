"""Tests for EMA retest entry logic."""
import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.ema import compute_ema, compute_ema_series, compute_ema_slope
from blofin_bridge.poller import PositionPoller
from blofin_bridge.state import Store


# === EMA computation ===


def test_ema_basic():
    closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0]
    ema = compute_ema(closes, 9)
    assert ema == pytest.approx(14.0)  # SMA of all 9 = 14


def test_ema_with_extra_bars():
    closes = [10.0] * 9 + [20.0]
    ema = compute_ema(closes, 9)
    # SMA seed = 10, then one step: (20 - 10) * 0.2 + 10 = 12.0
    assert ema == pytest.approx(12.0)


def test_ema_period_too_long():
    with pytest.raises(ValueError, match="need at least"):
        compute_ema([1.0, 2.0], 9)


# === EMA series + slope ===


def test_compute_ema_series_matches_final_ema():
    closes = [10.0] * 9 + [20.0, 30.0]
    series = compute_ema_series(closes, 9)
    # First EMA is the SMA seed at index period-1 = 8
    assert len(series) == len(closes) - 9 + 1  # 3 values
    assert series[0] == pytest.approx(10.0)
    assert series[-1] == pytest.approx(compute_ema(closes, 9))


def test_compute_ema_slope_positive_when_rising():
    closes = [10.0] * 9 + [20.0]
    slope = compute_ema_slope(closes, period=9, lookback=1)
    assert slope > 0


def test_compute_ema_slope_negative_when_falling():
    closes = [100.0] * 9 + [50.0]
    slope = compute_ema_slope(closes, period=9, lookback=1)
    assert slope < 0


def test_compute_ema_slope_flat():
    closes = [100.0] * 15
    slope = compute_ema_slope(closes, period=9, lookback=1)
    assert slope == pytest.approx(0.0)


# === EMA retest poller integration ===


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ema.db")


@pytest.fixture
def blofin():
    m = MagicMock()
    m.fetch_positions.return_value = []
    m.fetch_last_price.return_value = 100.0
    m.place_sl_order.return_value = "sl-id"
    m.place_limit_reduce_only.return_value = "tp-id"
    m.place_market_entry.return_value = {
        "orderId": "e-1", "fill_price": 100.0, "filled": 10,
    }
    m.get_instrument.return_value = {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }
    return m


def _sym_cfg():
    return {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 30,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "sl_loss_usdt": 15, "trail_activate_usdt": 25,
            "trail_distance_usdt": 10, "tp_limit_margin_pct": 2.0,
            "ema_retest_period": 9, "ema_retest_timeframe": "5m",
            "atr_length": 14, "ema_slope_lookback": 1,
            "max_signal_age_seconds": 900, "max_signal_bars": 3,
        },
    }


def _make_poller(store, blofin, **overrides):
    defaults = dict(
        store=store, blofin=blofin, interval_seconds=0,
        breakeven_usdt=15, trail_activate_usdt=25,
        trail_start_usdt=30, trail_distance_usdt=10,
        margin_usdt=100, leverage=30,
        ema_retest_period=9, ema_retest_timeframe="5m",
        symbol_configs=_sym_cfg(),
        # Revalidation config (loose by default so existing tests still pass;
        # individual tests override where they want strictness).
        max_signal_age_seconds=86400,
        max_signal_bars=1000,
        max_price_drift_percent=100.0,
        use_atr_drift_filter=False,
        max_price_drift_atr=0.5,
        require_retest_confirmation_candle=False,
        cancel_on_slope_flip=False,
        atr_length=14,
        ema_slope_lookback=1,
    )
    defaults.update(overrides)
    return PositionPoller(**defaults)


def _bars_with_ema_at(ema_target: float, num_bars: int = 15):
    """Generate flat bars where close = ema_target, so EMA(9) = ema_target."""
    return [[1700000000 + i * 300, ema_target, ema_target + 0.1,
             ema_target - 0.1, ema_target, 1000.0]
            for i in range(num_bars)]


@pytest.mark.asyncio
async def test_pending_buy_fills_on_ema_retest(store, blofin):
    """Price at or below EMA(9) triggers the pending buy."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )

    # After entry, BloFin will report the position
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = _bars_with_ema_at(100.0)

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    signals = store.list_pending_signals()
    assert len(signals) == 0

    pos = store.get_open_position("SOL-USDT")
    assert pos is not None
    assert pos.side == "long"


@pytest.mark.asyncio
async def test_pending_buy_waits_when_above_ema(store, blofin):
    """Price above EMA(9) → don't enter yet."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )

    # EMA at 100, price at 102 → above EMA → no retest
    blofin.fetch_last_price.return_value = 102.0
    blofin.fetch_recent_ohlcv.return_value = _bars_with_ema_at(100.0)

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    # Signal still pending
    signals = store.list_pending_signals()
    assert len(signals) == 1

    # No position opened
    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_pending_sell_fills_on_ema_retest(store, blofin):
    """Price at or above EMA(9) triggers the pending sell."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="sell", signal_price=95.0, timeout_minutes=30,
    )

    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = _bars_with_ema_at(100.0)
    blofin.place_market_entry.return_value = {
        "orderId": "e-2", "fill_price": 100.0, "filled": 10,
    }

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    pos = store.get_open_position("SOL-USDT")
    assert pos is not None
    assert pos.side == "short"


@pytest.mark.asyncio
async def test_pending_signal_expires(store, blofin):
    """Signal past its timeout gets expired."""
    # Create with 0 minutes timeout → already expired
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=0,
    )

    import time
    time.sleep(0.1)  # ensure we're past expiry

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    signals = store.list_pending_signals()
    assert len(signals) == 0
    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_new_signal_cancels_previous(store, blofin):
    """A new buy signal cancels any existing pending for that symbol."""
    from blofin_bridge.router import dispatch

    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = _bars_with_ema_at(100.0, num_bars=30)
    cfg = _sym_cfg()

    # First buy
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1

    # Second buy — should cancel first
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1  # only the new one


# =============================================================================
#                  NEW: invalidation + revalidation pipeline
# =============================================================================

def _bars_trending_up(ema_target: float, num_bars: int = 30):
    """Gently rising bars so slope > 0."""
    return [[1_700_000_000_000 + i * 300_000,
             ema_target + i * 0.01, ema_target + i * 0.01 + 0.1,
             ema_target + i * 0.01 - 0.1, ema_target + i * 0.01, 1000.0]
            for i in range(num_bars)]


def _snap_buy(store, *, candle_low=99.0, candle_high=101.0, signal_price=100.0,
              signal_ema=100.0, signal_slope=0.1, signal_atr=1.0,
              bar_ts=1_700_000_000_000, max_age=900, max_bars=3):
    """Helper: create a pending buy signal with a full snapshot."""
    return store.create_pending_signal(
        symbol="SOL-USDT", action="buy",
        signal_price=signal_price, timeout_minutes=15,
        signal_timeframe="5m",
        signal_candle_high=candle_high,
        signal_candle_low=candle_low,
        signal_ema_value=signal_ema,
        signal_ema_slope=signal_slope,
        signal_atr=signal_atr,
        signal_bar_ts=bar_ts,
        max_age_seconds=max_age,
        max_bars=max_bars,
    )


@pytest.mark.asyncio
async def test_pending_invalidated_by_structure_break(store, blofin):
    """If any closed bar after the signal prints below signal_candle_low, kill the signal."""
    _snap_buy(store, candle_low=99.0, signal_price=100.0, signal_ema=100.0)

    # Bars AFTER the signal bar that include a close < 99.0 (structure broken).
    bars_after_signal = [
        [1_700_000_000_000 + (1) * 300_000, 99.5, 99.7, 98.8, 98.9, 1000.0],  # close=98.9 < 99.0
    ]
    blofin.fetch_last_price.return_value = 99.5
    # fetch_recent_ohlcv should return signal bar + after bars + enough history for EMA
    history = [[1_700_000_000_000 - (15 - i) * 300_000, 100.0, 100.1, 99.9, 100.0, 1000.0]
               for i in range(15)]
    signal_bar = [[1_700_000_000_000, 100.0, 101.0, 99.0, 100.0, 1000.0]]
    blofin.fetch_recent_ohlcv.return_value = history + signal_bar + bars_after_signal

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    # Signal no longer pending
    assert store.list_pending_signals() == []
    # Recorded as invalidated with correct reason
    all_sigs = store.list_all_signals()
    assert all_sigs[0]["status"] == "invalidated"
    assert all_sigs[0]["cancel_reason"] == "invalidated_structure_break"


@pytest.mark.asyncio
async def test_pending_invalidated_by_slope_flip(store, blofin):
    """With cancel_on_slope_flip=True, a flipped slope kills a long signal."""
    _snap_buy(store, signal_slope=0.1)

    # Build declining bars → slope flips negative.
    closes = [100.0] * 8 + [99.8, 99.5, 99.2, 98.9, 98.6]
    bars = [[1_700_000_000_000 - (len(closes) - 1 - i) * 300_000,
             c, c + 0.1, c - 0.1, c, 1000.0] for i, c in enumerate(closes)]
    blofin.fetch_last_price.return_value = 98.6
    blofin.fetch_recent_ohlcv.return_value = bars

    poller = _make_poller(store, blofin, cancel_on_slope_flip=True)
    await poller.poll_once()

    assert store.list_pending_signals() == []
    assert store.list_all_signals()[0]["cancel_reason"] == "invalidated_slope_flip"


@pytest.mark.asyncio
async def test_pending_invalidated_by_price_drift_percent(store, blofin):
    """Price drifts >0.35% from signal price → kill."""
    _snap_buy(store, signal_price=100.0)

    bars = _bars_with_ema_at(100.4, num_bars=30)
    blofin.fetch_last_price.return_value = 100.5  # 0.5% drift
    blofin.fetch_recent_ohlcv.return_value = bars

    poller = _make_poller(
        store, blofin,
        max_price_drift_percent=0.35,
        use_atr_drift_filter=False,
        cancel_on_slope_flip=False,
    )
    await poller.poll_once()

    assert store.list_pending_signals() == []
    assert store.list_all_signals()[0]["cancel_reason"] == "invalidated_price_drift"


@pytest.mark.asyncio
async def test_pending_expired_by_bar_limit(store, blofin):
    """After max_signal_bars elapsed, the signal expires even without structure break."""
    bar_ts = 1_700_000_000_000
    _snap_buy(store, bar_ts=bar_ts, max_bars=3)

    # 5 bars forward → 5 > max_bars=3 → expired_bar_limit
    latest_ts = bar_ts + 5 * 300_000
    bars = [[latest_ts - (29 - i) * 300_000, 100.0, 100.1, 99.9, 100.0, 1000.0]
            for i in range(30)]
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = bars

    poller = _make_poller(store, blofin, max_signal_bars=3)
    await poller.poll_once()

    assert store.list_pending_signals() == []
    all_sigs = store.list_all_signals()
    assert all_sigs[0]["status"] == "expired"
    assert all_sigs[0]["cancel_reason"] == "expired_bar_limit"


@pytest.mark.asyncio
async def test_retest_without_confirmation_candle_does_not_enter(store, blofin):
    """With require_retest_confirmation_candle=True, a retest where the closed
    bar closes BELOW EMA (bearish context) must NOT open a long position."""
    _snap_buy(store, candle_low=99.0)

    # EMA at 100, last closed bar closes at 99.5 (below EMA), price touches EMA.
    bars = []
    for i in range(29):
        bars.append([1_700_000_000_000 - (29 - i) * 300_000,
                     100.0, 100.1, 99.9, 100.0, 1000.0])
    # Last bar closed BELOW EMA
    bars.append([1_700_000_000_000, 100.0, 100.1, 99.4, 99.5, 1000.0])

    blofin.fetch_last_price.return_value = 100.0  # touching EMA
    blofin.fetch_recent_ohlcv.return_value = bars

    poller = _make_poller(
        store, blofin,
        require_retest_confirmation_candle=True,
        cancel_on_slope_flip=False,
    )
    await poller.poll_once()

    # Position NOT opened, signal remains pending (awaiting a proper confirmation)
    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_retest_with_confirmation_candle_does_enter(store, blofin):
    """Happy path: confirmation candle closes above EMA → enter."""
    _snap_buy(store, candle_low=99.0)

    bars = []
    for i in range(29):
        bars.append([1_700_000_000_000 - (29 - i) * 300_000,
                     100.0, 100.1, 99.9, 100.0, 1000.0])
    # Last bar CLOSED above EMA (bullish rejection of EMA support)
    bars.append([1_700_000_000_000, 99.9, 100.6, 99.7, 100.4, 1000.0])

    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = bars
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]
    blofin.place_market_entry.return_value = {
        "orderId": "e-1", "fill_price": 100.0, "filled": 10,
    }

    poller = _make_poller(
        store, blofin,
        require_retest_confirmation_candle=True,
        cancel_on_slope_flip=False,
    )
    await poller.poll_once()

    pos = store.get_open_position("SOL-USDT")
    assert pos is not None
    assert pos.side == "long"
