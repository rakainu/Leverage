import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.poller import PositionPoller, _detect_tp_fill
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "poller.db")


@pytest.fixture
def blofin():
    m = MagicMock()
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


def test_detect_tp_fill_returns_filled_stages():
    """If fetch_order says closed for TP1, _detect_tp_fill returns [1]."""
    order_statuses = {
        "tp1-id": {"status": "closed", "filled": 5.0, "average": 101.0},
        "tp2-id": {"status": "open", "filled": 0.0},
        "tp3-id": {"status": "open", "filled": 0.0},
    }
    filled = _detect_tp_fill(
        tp1_order_id="tp1-id", tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        fetch_fn=lambda oid: order_statuses[oid],
    )
    assert filled == [1]


def test_detect_tp_fill_multiple():
    order_statuses = {
        "tp1-id": {"status": "closed", "filled": 5.0, "average": 101.0},
        "tp2-id": {"status": "closed", "filled": 3.75, "average": 102.0},
        "tp3-id": {"status": "open"},
    }
    filled = _detect_tp_fill(
        tp1_order_id="tp1-id", tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        fetch_fn=lambda oid: order_statuses[oid],
    )
    assert filled == [1, 2]


def test_detect_tp_fill_skips_already_cleared():
    """If tp1_order_id is None (already processed), skip it."""
    order_statuses = {
        "tp2-id": {"status": "closed", "filled": 3.75, "average": 102.0},
    }
    filled = _detect_tp_fill(
        tp1_order_id=None, tp2_order_id="tp2-id", tp3_order_id="tp3-id",
        fetch_fn=lambda oid: order_statuses.get(oid, {"status": "open"}),
    )
    assert filled == [2]


async def test_poller_tp1_fill_moves_sl_to_entry(store, blofin):
    pid = _long_position_with_tps(store)
    blofin.fetch_order.side_effect = lambda oid, inst: {
        "tp1-id": {"status": "closed", "filled": 5.0, "average": 101.0},
        "tp2-id": {"status": "open"},
        "tp3-id": {"status": "open"},
    }[oid]
    blofin.cancel_tpsl.return_value = None
    blofin.place_sl_order.return_value = "sl-breakeven"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.tp1_order_id is None
    assert row.tp1_fill_price == pytest.approx(101.0)
    assert row.current_size == pytest.approx(7.5)
    assert row.sl_order_id == "sl-breakeven"

    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "sl-init")
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(100.0)
    assert kwargs["side"] == "sell"


async def test_poller_tp2_fill_moves_sl_to_tp1_price(store, blofin):
    pid = _long_position_with_tps(store)
    store.record_tp_fill(pid, stage=1, fill_price=101.0, closed_contracts=5.0)
    store.clear_tp_order_id(pid, stage=1)
    store.record_sl_order_id(pid, "sl-breakeven")

    blofin.fetch_order.side_effect = lambda oid, inst: {
        "tp2-id": {"status": "closed", "filled": 3.75, "average": 102.0},
        "tp3-id": {"status": "open"},
    }[oid]
    blofin.place_sl_order.return_value = "sl-tp1-lock"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 2
    assert row.tp2_order_id is None
    assert row.tp2_fill_price == pytest.approx(102.0)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(101.0)


async def test_poller_tp3_fill_archives_position(store, blofin):
    pid = _long_position_with_tps(store)
    store.record_tp_fill(pid, stage=1, fill_price=101.0, closed_contracts=5.0)
    store.clear_tp_order_id(pid, stage=1)
    store.record_tp_fill(pid, stage=2, fill_price=102.0, closed_contracts=3.75)
    store.clear_tp_order_id(pid, stage=2)
    store.record_sl_order_id(pid, "sl-tp1-lock")

    blofin.fetch_order.side_effect = lambda oid, inst: {
        "tp3-id": {"status": "closed", "filled": 3.75, "average": 103.0},
    }[oid]

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is None
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "sl-tp1-lock")


async def test_poller_no_fills_is_noop(store, blofin):
    pid = _long_position_with_tps(store)
    blofin.fetch_order.side_effect = lambda oid, inst: {"status": "open"}

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 0
    assert row.tp1_order_id == "tp1-id"
    blofin.cancel_tpsl.assert_not_called()
    blofin.place_sl_order.assert_not_called()


async def test_poller_short_tp1_fill_moves_sl_to_entry(store, blofin):
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=100.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    store.record_tp_order_ids(
        pid, tp1_order_id="stp1", tp2_order_id="stp2", tp3_order_id="stp3",
    )
    blofin.fetch_order.side_effect = lambda oid, inst: {
        "stp1": {"status": "closed", "filled": 5.0, "average": 99.0},
        "stp2": {"status": "open"},
        "stp3": {"status": "open"},
    }[oid]
    blofin.place_sl_order.return_value = "sl-breakeven"

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["side"] == "buy"
    assert kwargs["trigger_price"] == pytest.approx(100.0)


async def test_poller_swallows_exceptions(store, blofin):
    """A single position failure should not crash the poll loop."""
    pid = _long_position_with_tps(store)
    blofin.fetch_order.side_effect = Exception("ccxt boom")

    poller = PositionPoller(store=store, blofin=blofin, interval_seconds=0)
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 0
