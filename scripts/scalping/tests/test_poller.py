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
        trail_activate_usdt=25, trail_start_usdt=30,
        trail_distance_usdt=10, margin_usdt=100, leverage=30,
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


# === Phase 1: SL jump at trail_activate ($25) ===


@pytest.mark.asyncio
async def test_sl_jumps_at_activate_threshold(store, blofin):
    """At +$25 profit, SL jumps to lock in $20 (trail_start - trail_distance)."""
    pid = _long_position(store, entry_price=300.0)
    # $25 profit → price = 300 + (25/3000)*300 = 302.5
    blofin.fetch_last_price.return_value = 302.5

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1  # jumped, not trailing yet

    # SL should lock in $20 profit: entry + (20/3000)*300 = 302.0
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 300.0 + (20 / 3000) * 300.0  # 302.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


@pytest.mark.asyncio
async def test_no_jump_below_activate_threshold(store, blofin):
    """Below +$25 profit, nothing happens."""
    pid = _long_position(store, entry_price=300.0)
    # $20 profit → price = 302.0
    blofin.fetch_last_price.return_value = 302.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 0
    blofin.place_sl_order.assert_not_called()


# === Phase 2: Dead zone ($25 to $30) ===


@pytest.mark.asyncio
async def test_sl_locked_in_dead_zone(store, blofin):
    """Between $25 and $30 profit, SL stays locked. No movement."""
    pid = _long_position(store, entry_price=300.0)
    # Simulate: already jumped (trail_active=1)
    store.update_trail(pid, trail_high_price=302.5, trail_active=1)

    # Price at $28 profit = 302.8 — in the dead zone
    blofin.fetch_last_price.return_value = 302.8

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1  # still locked
    blofin.place_sl_order.assert_not_called()
    blofin.cancel_all_tpsl.assert_not_called()


@pytest.mark.asyncio
async def test_sl_locked_even_at_29_99(store, blofin):
    """Just below $30 profit — still in dead zone."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=302.5, trail_active=1)

    # $29.99 profit → 302.999
    blofin.fetch_last_price.return_value = 302.999

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1  # still locked
    blofin.place_sl_order.assert_not_called()


# === Phase 3: Trail starts at $30+ ===


@pytest.mark.asyncio
async def test_trail_starts_at_trail_start_threshold(store, blofin):
    """At +$30 profit, trail transitions from locked to trailing."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=302.5, trail_active=1)

    # $30 profit → 303.0
    blofin.fetch_last_price.return_value = 303.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 2  # now trailing


@pytest.mark.asyncio
async def test_trail_moves_sl_on_new_high(store, blofin):
    """Once trailing (state 2), new high moves SL."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=303.0, trail_active=2)
    store.record_sl_order_id(pid, "sl-old")

    # Price makes new high at 306
    blofin.fetch_last_price.return_value = 306.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(306.0)
    assert row.trail_active == 2

    # SL at 306 - trail_distance_as_price
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 306.0 - (10 / 3000) * 306.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


@pytest.mark.asyncio
async def test_trail_does_not_move_sl_on_lower_price(store, blofin):
    """If price drops below the high while trailing, SL stays."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=306.0, trail_active=2)
    store.record_sl_order_id(pid, "sl-current")

    # Price drops to 304
    blofin.fetch_last_price.return_value = 304.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(306.0)
    blofin.place_sl_order.assert_not_called()
    blofin.cancel_all_tpsl.assert_not_called()


# === Short position ===


@pytest.mark.asyncio
async def test_short_sl_jumps_at_activate(store, blofin):
    """Short: SL jumps at +$25 profit, locks in $20."""
    pid = _short_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]
    # $25 profit on short → price = 300 - (25/3000)*300 = 297.5
    blofin.fetch_last_price.return_value = 297.5

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1

    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    # Lock in $20: entry - (20/3000)*300 = 298.0
    expected_sl = 300.0 - (20 / 3000) * 300.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)
    assert kwargs["side"] == "buy"


@pytest.mark.asyncio
async def test_short_trail_moves_on_new_low(store, blofin):
    """Short trailing: SL follows when price makes new low."""
    pid = _short_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=297.0, trail_active=2)
    store.record_sl_order_id(pid, "sl-old")
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
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


# === Full lifecycle ===


@pytest.mark.asyncio
async def test_full_lifecycle_jump_deadzone_trail(store, blofin):
    """Complete flow: inactive → jump → dead zone → trailing."""
    pid = _long_position(store, entry_price=300.0)
    poller = _make_poller(store, blofin)

    # Cycle 1: $25 profit → SL jumps, state=1
    blofin.fetch_last_price.return_value = 302.5
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    assert blofin.place_sl_order.call_count == 1

    # Cycle 2: $28 profit → dead zone, no movement
    blofin.fetch_last_price.return_value = 302.8
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    blofin.place_sl_order.assert_not_called()

    # Cycle 3: $31 profit → trail starts (state=2), SL moves
    blofin.fetch_last_price.return_value = 303.1
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 2

    # Cycle 4: $40 profit → trail follows
    blofin.fetch_last_price.return_value = 304.0
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(304.0)
    blofin.place_sl_order.assert_called_once()
