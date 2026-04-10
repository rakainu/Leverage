"""Tests for EMA retest entry logic."""
import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.ema import compute_ema
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
    cfg = _sym_cfg()

    # First buy
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1

    # Second buy — should cancel first
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1  # only the new one
