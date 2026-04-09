import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.poller import PositionPoller
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "poller.db")


@pytest.fixture
def blofin():
    m = MagicMock()
    m.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"},
        "symbol": "SOL/USDT:USDT",
        "contracts": 10,
        "side": "long",
    }]
    m.fetch_last_price.return_value = 300.0
    m.place_sl_order.return_value = "sl-trail-id"
    return m


def _make_poller(store, blofin, **overrides):
    defaults = dict(
        store=store, blofin=blofin, interval_seconds=0,
        trail_activate_usdt=30, trail_distance_usdt=10,
        margin_usdt=100, leverage=30,
    )
    defaults.update(overrides)
    return PositionPoller(**defaults)


def _long_position(store, entry_price=300.0):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=entry_price,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    return pid


def _short_position(store, entry_price=300.0):
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=entry_price,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    return pid


# === Trail activation ===


@pytest.mark.asyncio
async def test_trail_activates_at_threshold(store, blofin):
    """When profit hits $30, trail should activate."""
    pid = _long_position(store, entry_price=300.0)
    # $30 profit on $100@30x=$3000 notional → need price at 300 + (30/3000)*300 = 303.0
    blofin.fetch_last_price.return_value = 303.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    assert row.trail_high_price == pytest.approx(303.0)

    # SL placed at current - trail_distance_as_price
    # trail_distance = (10/3000)*303 = 1.01
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 303.0 - (10 / 3000) * 303.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


@pytest.mark.asyncio
async def test_trail_does_not_activate_below_threshold(store, blofin):
    """Below $30 profit, trail should NOT activate."""
    pid = _long_position(store, entry_price=300.0)
    # $20 profit → price = 302.0
    blofin.fetch_last_price.return_value = 302.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 0
    assert row.trail_high_price is None
    blofin.place_sl_order.assert_not_called()


@pytest.mark.asyncio
async def test_trail_moves_sl_on_new_high(store, blofin):
    """Once trail is active, new high should move SL up."""
    pid = _long_position(store, entry_price=300.0)
    # Manually activate trail
    store.update_trail(pid, trail_high_price=303.0, trail_active=True)
    store.record_sl_order_id(pid, "sl-old")

    # Price makes new high at 306
    blofin.fetch_last_price.return_value = 306.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(306.0)

    # SL should be at 306 - trail_distance_as_price
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 306.0 - (10 / 3000) * 306.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


@pytest.mark.asyncio
async def test_trail_does_not_move_sl_on_lower_price(store, blofin):
    """If price drops below the high, SL should NOT move."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=306.0, trail_active=True)
    store.record_sl_order_id(pid, "sl-current")

    # Price drops to 304 — below the 306 high
    blofin.fetch_last_price.return_value = 304.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    # High should remain at 306, SL not touched
    assert row.trail_high_price == pytest.approx(306.0)
    blofin.place_sl_order.assert_not_called()
    blofin.cancel_all_tpsl.assert_not_called()


# === Short position trailing ===


@pytest.mark.asyncio
async def test_short_trail_activates(store, blofin):
    """Short position: trail activates when price drops enough."""
    pid = _short_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"},
        "contracts": 10,
    }]
    # $30 profit on short → price needs to drop: 300 - (30/3000)*300 = 297.0
    blofin.fetch_last_price.return_value = 297.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    assert row.trail_high_price == pytest.approx(297.0)

    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    # SL above: 297 + trail_distance
    expected_sl = 297.0 + (10 / 3000) * 297.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)
    assert kwargs["side"] == "buy"


@pytest.mark.asyncio
async def test_short_trail_moves_on_new_low(store, blofin):
    """Short: SL moves down when price makes new low."""
    pid = _short_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=297.0, trail_active=True)
    store.record_sl_order_id(pid, "sl-old")
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"},
        "contracts": 10,
    }]

    blofin.fetch_last_price.return_value = 294.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(294.0)
    blofin.place_sl_order.assert_called_once()


# === Drift detection ===


@pytest.mark.asyncio
async def test_archives_stale_position_when_blofin_flat(store, blofin):
    pid = _long_position(store)
    blofin.fetch_positions.return_value = []

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_skips_drift_check_if_fetch_positions_fails(store, blofin):
    pid = _long_position(store)
    blofin.fetch_positions.side_effect = Exception("ccxt boom")

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is not None


@pytest.mark.asyncio
async def test_swallows_exceptions_per_position(store, blofin):
    pid = _long_position(store)
    blofin.fetch_last_price.side_effect = Exception("ccxt boom")

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.trail_active == 0


# === Profit calculation verification ===


@pytest.mark.asyncio
async def test_exact_profit_threshold_activates(store, blofin):
    """Exactly $30 profit should activate trail."""
    pid = _long_position(store, entry_price=300.0)
    # Exactly $30: (30/3000)*300 = 3.0 price move → 303.0
    blofin.fetch_last_price.return_value = 303.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1


@pytest.mark.asyncio
async def test_just_below_threshold_does_not_activate(store, blofin):
    """$29.99 profit should NOT activate trail."""
    pid = _long_position(store, entry_price=300.0)
    # Just under $30: price = 302.99
    blofin.fetch_last_price.return_value = 302.99

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 0
