"""Tests for EMA retest entry logic."""
import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.ema import compute_ema, compute_ema_series
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
    m.place_limit_entry.return_value = {"orderId": "lim-1", "price": 100.0}
    m.list_pending_tpsl.return_value = [{"tpslId": "sl-7", "slTriggerPrice": "99.0"}]
    m.cancel_order.return_value = None
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


def _flat_bars(ema_target: float, num_bars: int = 15):
    """Perfectly flat bars: EMA(9) == ema_target, EMA slope == 0."""
    return [[1700000000 + i * 300, ema_target, ema_target + 0.1,
             ema_target - 0.1, ema_target, 1000.0]
            for i in range(num_bars)]


def _bars_with_upward_slope(target_price: float, num_bars: int = 15):
    """Bars whose closes step gently upward at the end so EMA(9) ends just
    above target_price, with a 3-bar EMA slope comfortably above the 0.03%
    flat-slope gate. Useful for buy-retest tests (current_price <= EMA)."""
    closes = [target_price] * (num_bars - 4) + [
        target_price * 1.0005,
        target_price * 1.0010,
        target_price * 1.0015,
        target_price * 1.0020,
    ]
    return [[1700000000 + i * 300, c, c + 0.1, c - 0.1, c, 1000.0]
            for i, c in enumerate(closes)]


def _bars_with_downward_slope(target_price: float, num_bars: int = 15):
    """Bars whose closes step gently downward at the end so EMA(9) ends just
    below target_price, with a 3-bar EMA slope comfortably above the 0.03%
    flat-slope gate. Useful for sell-retest tests (current_price >= EMA)."""
    closes = [target_price] * (num_bars - 4) + [
        target_price * 0.9995,
        target_price * 0.9990,
        target_price * 0.9985,
        target_price * 0.9980,
    ]
    return [[1700000000 + i * 300, c, c + 0.1, c - 0.1, c, 1000.0]
            for i, c in enumerate(closes)]


# Backwards-compatible alias for the few callers that pre-date the slope gate.
def _bars_with_ema_at(ema_target: float, num_bars: int = 15):
    return _bars_with_upward_slope(ema_target, num_bars)


# === Resting-EMA9-limit entry lifecycle (Plan A: match the engine's retest) ===
#
# The engine fires when a bar's low/high TOUCHES EMA9 (intrabar) and fills at
# EMA9. Live can't catch that by polling last-price every 2s, so instead it
# rests a limit at EMA9 while the signal is live: the wick fills it at EMA9.


@pytest.mark.asyncio
async def test_pending_buy_places_resting_limit_at_ema(store, blofin):
    """A fresh pending buy that passes gates rests a limit at EMA9 (no market entry)."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    blofin.fetch_positions.return_value = []           # nothing filled yet
    blofin.fetch_recent_ohlcv.return_value = _flat_bars(100.0)  # EMA9 == 100
    blofin.place_limit_entry.return_value = {"orderId": "lim-7", "price": 100.0}

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    blofin.place_limit_entry.assert_called_once()
    _, kw = blofin.place_limit_entry.call_args
    assert kw["side"] == "buy"
    assert kw["price"] == pytest.approx(100.0, abs=0.2)
    blofin.place_market_entry.assert_not_called()      # NOT a market entry
    sig = store.list_pending_signals()[0]
    assert sig["limit_order_id"] == "lim-7"
    assert store.get_open_position("SOL-USDT") is None  # not filled yet


@pytest.mark.asyncio
async def test_resting_limit_finalizes_when_filled(store, blofin):
    """When BloFin reports a position, the resting limit is treated as filled:
    a position row is created at the rested EMA9 and the pending is consumed."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    store.record_pending_limit(sid, order_id="lim-7", price=100.0)
    blofin.fetch_positions.return_value = [
        {"info": {"instId": "SOL-USDT"}, "contracts": 10},
    ]

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    pos = store.get_open_position("SOL-USDT")
    assert pos is not None
    assert pos.side == "long"
    assert pos.entry_price == pytest.approx(100.0)     # filled at the rested EMA9
    assert pos.sl_order_id == "sl-7"                   # attached SL captured
    assert store.list_pending_signals() == []          # pending consumed


@pytest.mark.asyncio
async def test_resting_limit_refreshes_when_ema_moves(store, blofin):
    """A new closed bar moves EMA9 → cancel the stale limit and re-place at the
    new EMA9 (mirrors the engine refreshing ema[i] each bar)."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    store.record_pending_limit(sid, order_id="lim-old", price=100.0)
    blofin.fetch_positions.return_value = []
    blofin.fetch_recent_ohlcv.return_value = _flat_bars(101.0)   # EMA9 now 101
    blofin.place_limit_entry.return_value = {"orderId": "lim-new", "price": 101.0}

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    blofin.cancel_order.assert_called_once_with("lim-old", "SOL-USDT")
    blofin.place_limit_entry.assert_called_once()
    sig = store.list_pending_signals()[0]
    assert sig["limit_order_id"] == "lim-new"
    assert sig["limit_price"] == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_resting_limit_not_replaced_when_ema_unchanged(store, blofin):
    """Within the same bar EMA9 is constant → don't churn the resting limit."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    store.record_pending_limit(sid, order_id="lim-keep", price=100.0)
    blofin.fetch_positions.return_value = []
    blofin.fetch_recent_ohlcv.return_value = _flat_bars(100.0)   # EMA9 still 100

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    blofin.cancel_order.assert_not_called()
    blofin.place_limit_entry.assert_not_called()
    assert store.list_pending_signals()[0]["limit_order_id"] == "lim-keep"


@pytest.mark.asyncio
async def test_expired_pending_cancels_resting_limit(store, blofin):
    """Past timeout → cancel the resting limit and expire the signal."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=0,
    )
    store.record_pending_limit(sid, order_id="lim-x", price=100.0)
    import time
    time.sleep(0.1)
    blofin.fetch_positions.return_value = []

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    blofin.cancel_order.assert_called_once_with("lim-x", "SOL-USDT")
    assert store.list_pending_signals() == []
    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_flat_slope_blocks_limit_placement_keeps_pending(store, blofin):
    """Slope gate fails → no limit rested, pending stays alive for a later bar."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    blofin.fetch_positions.return_value = []
    blofin.fetch_recent_ohlcv.return_value = _flat_bars(100.0)   # zero slope

    poller = _make_poller(store, blofin, min_5m_slope_pct=0.03)
    await poller.poll_once()

    blofin.place_limit_entry.assert_not_called()
    assert len(store.list_pending_signals()) == 1


@pytest.mark.asyncio
async def test_gate_failure_cancels_existing_resting_limit(store, blofin):
    """If gates start failing while a limit rests, pull the limit (don't fill on
    a bad bar) but keep the pending alive."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0, timeout_minutes=30,
    )
    store.record_pending_limit(sid, order_id="lim-bad", price=100.0)
    blofin.fetch_positions.return_value = []
    blofin.fetch_recent_ohlcv.return_value = _flat_bars(100.0)   # zero slope now

    poller = _make_poller(store, blofin, min_5m_slope_pct=0.03)
    await poller.poll_once()

    blofin.cancel_order.assert_called_once_with("lim-bad", "SOL-USDT")
    sig = store.list_pending_signals()[0]
    assert sig["limit_order_id"] is None


@pytest.mark.asyncio
async def test_new_signal_cancels_previous(store, blofin):
    """A new buy signal cancels any existing pending for that symbol (router)."""
    from blofin_bridge.router import dispatch
    blofin.fetch_last_price.return_value = 100.0
    cfg = _sym_cfg()
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1
    dispatch(action="buy", symbol="SOL-USDT", store=store, blofin=blofin, symbol_configs=cfg)
    assert len(store.list_pending_signals()) == 1
