from datetime import datetime, timezone

import pytest

from blofin_bridge.state import Store, PositionRow


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


def test_create_and_fetch_open_position(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    assert pid > 0

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.id == pid
    assert row.symbol == "SOL-USDT"
    assert row.side == "long"
    assert row.entry_price == 80.0
    assert row.initial_size == 12
    assert row.current_size == 12
    assert row.tp_stage == 0
    assert row.closed_at is None


def test_get_open_position_returns_none_when_flat(store):
    assert store.get_open_position("SOL-USDT") is None


def test_record_tp_fill_updates_stage_and_size(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_tp_fill(pid, stage=1, fill_price=82.0, closed_contracts=4)
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.tp1_fill_price == 82.0
    assert row.current_size == 8


def test_close_position_sets_closed_at(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.close_position(pid, realized_pnl=42.5)
    assert store.get_open_position("SOL-USDT") is None
    closed = store.get_position(pid)
    assert closed.closed_at is not None
    assert closed.realized_pnl == 42.5


def test_record_sl_order_id(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "algo-123")
    row = store.get_position(pid)
    assert row.sl_order_id == "algo-123"


def test_append_event_and_update_outcome(store):
    eid = store.append_event(
        position_id=None, event_type="buy",
        payload='{"action":"buy"}',
    )
    store.mark_event_handled(eid, outcome="ok", error_msg=None)
    events = store.recent_events(limit=10)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"
