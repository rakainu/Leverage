import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.poller import PositionPoller, _detect_tp_fill
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "poller.db")


def _closed_order(oid, filled, avg, status="closed"):
    return {"id": oid, "status": status, "filled": filled, "average": avg}


@pytest.fixture
def blofin():
    m = MagicMock()
    # Default: BloFin still has the SOL position open, no TP fills in closed.
    m.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"},
        "symbol": "SOL/USDT:USDT",
        "contracts": 12.5,
        "side": "long",
    }]
    m.fetch_closed_orders.return_value = []   # nothing filled by default
    return m


def _long_position_with_tps(store, sl_id="sl-init", tp1="tp1-id", tp2="tp2-id", tp3="tp3-id"):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=100.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, sl_id)
    store.record_tp_order_ids(
        pid, tp1_order_id=tp1, tp2_order_id=tp2, tp3_order_id=tp3,
    )
    store.record_atr_context(pid, atr_value=1.0, sl_distance=3.0)
    return pid


# === _detect_tp_fill unit tests ===

def test_detect_tp_fill_returns_filled_stage():
    filled_orders = {
        "tp1-id": _closed_order("tp1-id", 5.0, 101.0),
    }
    filled = _detect_tp_fill(
        tp1_order_id="tp1-id", tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        filled_orders=filled_orders,
    )
    assert len(filled) == 1
    assert filled[0][0] == 1
    assert filled[0][1]["average"] == 101.0


def test_detect_tp_fill_multiple_stages():
    filled_orders = {
        "tp1-id": _closed_order("tp1-id", 5.0, 101.0),
        "tp2-id": _closed_order("tp2-id", 3.75, 102.0),
    }
    filled = _detect_tp_fill(
        tp1_order_id="tp1-id", tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        filled_orders=filled_orders,
    )
    assert [stage for stage, _ in filled] == [1, 2]


def test_detect_tp_fill_skips_already_cleared():
    filled_orders = {
        "tp2-id": _closed_order("tp2-id", 3.75, 102.0),
    }
    filled = _detect_tp_fill(
        tp1_order_id=None, tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        filled_orders=filled_orders,
    )
    assert [stage for stage, _ in filled] == [2]


def test_detect_tp_fill_none_when_nothing_filled():
    filled = _detect_tp_fill(
        tp1_order_id="tp1-id", tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        filled_orders={},
    )
    assert filled == []


# === PositionPoller integration tests ===

@pytest.mark.asyncio
async def test_poller_tp1_fill_moves_sl_to_entry(store, blofin):
    pid = _long_position_with_tps(store)
    blofin.fetch_closed_orders.return_value = [
        _closed_order("tp1-id", 5.0, 101.0),
    ]
    blofin.place_sl_order.return_value = "sl-breakeven"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.tp1_order_id is None
    assert row.tp1_fill_price == pytest.approx(101.0)
    assert row.current_size == pytest.approx(7.5)
    assert row.sl_order_id == "sl-breakeven"

    # Attached entry SL + any tracked SL -> swept via cancel_all_tpsl
    blofin.cancel_all_tpsl.assert_called_once_with("SOL-USDT")
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(100.0)
    assert kwargs["side"] == "sell"


@pytest.mark.asyncio
async def test_poller_tp2_fill_moves_sl_to_tp1_price(store, blofin):
    pid = _long_position_with_tps(store)
    store.record_tp_fill(pid, stage=1, fill_price=101.0, closed_contracts=5.0)
    store.clear_tp_order_id(pid, stage=1)
    store.record_sl_order_id(pid, "sl-breakeven")

    blofin.fetch_closed_orders.return_value = [
        _closed_order("tp2-id", 3.75, 102.0),
    ]
    blofin.place_sl_order.return_value = "sl-tp1-lock"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 2
    assert row.tp2_order_id is None
    assert row.tp2_fill_price == pytest.approx(102.0)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(101.0)


@pytest.mark.asyncio
async def test_poller_tp3_fill_archives_position(store, blofin):
    pid = _long_position_with_tps(store)
    store.record_tp_fill(pid, stage=1, fill_price=101.0, closed_contracts=5.0)
    store.clear_tp_order_id(pid, stage=1)
    store.record_tp_fill(pid, stage=2, fill_price=102.0, closed_contracts=3.75)
    store.clear_tp_order_id(pid, stage=2)
    store.record_sl_order_id(pid, "sl-tp1-lock")

    blofin.fetch_closed_orders.return_value = [
        _closed_order("tp3-id", 3.75, 103.0),
    ]

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is None
    blofin.cancel_all_tpsl.assert_called_once_with("SOL-USDT")


@pytest.mark.asyncio
async def test_poller_no_fills_is_noop(store, blofin):
    pid = _long_position_with_tps(store)
    # fetch_closed_orders default is [] in the fixture

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 0
    assert row.tp1_order_id == "tp1-id"
    blofin.cancel_all_tpsl.assert_not_called()
    blofin.place_sl_order.assert_not_called()


@pytest.mark.asyncio
async def test_poller_short_tp1_fill_moves_sl_to_entry(store, blofin):
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=100.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    store.record_tp_order_ids(
        pid, tp1_order_id="stp1", tp2_order_id="stp2", tp3_order_id="stp3",
    )
    blofin.fetch_closed_orders.return_value = [
        _closed_order("stp1", 5.0, 99.0),
    ]
    blofin.place_sl_order.return_value = "sl-breakeven"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["side"] == "buy"
    assert kwargs["trigger_price"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_poller_swallows_exceptions(store, blofin):
    """A single position failure should not crash the poll loop."""
    pid = _long_position_with_tps(store)
    blofin.fetch_closed_orders.side_effect = Exception("ccxt boom")

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 0


# === Drift detection (v1.1.1) ===

@pytest.mark.asyncio
async def test_poller_archives_stale_position_when_blofin_flat(store, blofin):
    """If BloFin shows no position for a SQLite-tracked symbol, archive it."""
    pid = _long_position_with_tps(store)
    blofin.fetch_positions.return_value = []

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is None
    blofin.fetch_closed_orders.assert_not_called()


@pytest.mark.asyncio
async def test_poller_drift_cancels_leftover_tps_and_sl(store, blofin):
    pid = _long_position_with_tps(store)
    blofin.fetch_positions.return_value = []

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    assert blofin.cancel_order.call_count == 3
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "sl-init")


@pytest.mark.asyncio
async def test_poller_proceeds_normally_when_position_still_open(store, blofin):
    pid = _long_position_with_tps(store)
    # Default fixture: position present, no fills
    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.tp_stage == 0


@pytest.mark.asyncio
async def test_poller_skips_drift_check_if_fetch_positions_fails(store, blofin):
    pid = _long_position_with_tps(store)
    blofin.fetch_positions.side_effect = Exception("ccxt boom")

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    # Position should NOT be archived (drift unverifiable)
    assert store.get_open_position("SOL-USDT") is not None


# === Deferred SL retry (v1.1.3) ===

@pytest.mark.asyncio
async def test_poller_retries_sl_after_initial_rejection_next_cycle(store, blofin):
    """If SL placement fails BOTH inside _process_position AND the in-cycle
    retry, the position stays naked. On the NEXT cycle, _ensure_sl_in_place
    retries again and succeeds once price cooperates."""
    pid = _long_position_with_tps(store)
    blofin.fetch_closed_orders.return_value = [
        _closed_order("tp1-id", 5.0, 101.0),
    ]
    # Cycle 1: both attempts (fill-path and ensure-path) fail
    # Cycle 2: fetch returns no fills, ensure-path succeeds
    err = Exception(
        "blofin {\"code\":\"102038\",\"msg\":\"SL trigger price should be lower\"}"
    )
    blofin.place_sl_order.side_effect = [err, err, "sl-breakeven-retry"]

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.sl_order_id is None   # naked after cycle 1

    # Cycle 2: no new fills, just the ensure-sl retry
    blofin.fetch_closed_orders.return_value = []
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.sl_order_id == "sl-breakeven-retry"


@pytest.mark.asyncio
async def test_poller_in_cycle_retry_succeeds_without_waiting(store, blofin):
    """Common path: _process_position's SL placement fails, but
    _ensure_sl_in_place at the end of the same cycle succeeds immediately."""
    pid = _long_position_with_tps(store)
    blofin.fetch_closed_orders.return_value = [
        _closed_order("tp1-id", 5.0, 101.0),
    ]
    err = Exception(
        "blofin {\"code\":\"102038\",\"msg\":\"SL trigger price should be lower\"}"
    )
    blofin.place_sl_order.side_effect = [err, "sl-breakeven-same-cycle"]

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.sl_order_id == "sl-breakeven-same-cycle"


@pytest.mark.asyncio
async def test_poller_ensure_sl_is_noop_when_stage_zero(store, blofin):
    """A position in tp_stage=0 uses the attached entry SL and should NOT
    trigger standalone SL placement."""
    pid = _long_position_with_tps(store)
    # No fills, tp_stage stays 0

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    blofin.place_sl_order.assert_not_called()
