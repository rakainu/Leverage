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


def test_record_tp_order_ids_and_read_back(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_tp_order_ids(
        pid, tp1_order_id="tp1-a", tp2_order_id="tp2-b", tp3_order_id="tp3-c",
    )
    row = store.get_position(pid)
    assert row.tp1_order_id == "tp1-a"
    assert row.tp2_order_id == "tp2-b"
    assert row.tp3_order_id == "tp3-c"


def test_record_atr_context(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_atr_context(pid, atr_value=0.17, sl_distance=0.51)
    row = store.get_position(pid)
    assert row.atr_value == pytest.approx(0.17)
    assert row.sl_distance == pytest.approx(0.51)


def test_clear_tp_order_id(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_tp_order_ids(
        pid, tp1_order_id="tp1-a", tp2_order_id="tp2-b", tp3_order_id="tp3-c",
    )
    store.clear_tp_order_id(pid, stage=1)
    row = store.get_position(pid)
    assert row.tp1_order_id is None
    assert row.tp2_order_id == "tp2-b"


def test_clear_tp_order_id_invalid_stage_raises(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12.5, sl_policy="p2_step_stop", source="pro_v3",
    )
    with pytest.raises(ValueError, match="invalid tp stage"):
        store.clear_tp_order_id(pid, stage=5)


def test_append_event_and_update_outcome(store):
    eid = store.append_event(
        position_id=None, event_type="buy",
        payload='{"action":"buy"}',
    )
    store.mark_event_handled(eid, outcome="ok", error_msg=None)
    events = store.recent_events(limit=10)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"


# ----------------------- pending signal snapshots -----------------------

def test_create_pending_signal_persists_full_snapshot(store):
    """Snapshot columns must survive a round-trip through the DB."""
    sig_id = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=105.0,
        timeout_minutes=15,
        signal_timeframe="5m",
        signal_candle_high=106.0, signal_candle_low=104.0,
        signal_ema_value=105.2, signal_ema_slope=0.05,
        signal_atr=1.2, signal_bar_ts=1_700_000_000_000,
        max_age_seconds=900, max_bars=3,
    )
    assert sig_id > 0

    rows = store.list_pending_signals()
    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "SOL-USDT"
    assert r["action"] == "buy"
    assert r["signal_price"] == 105.0
    assert r["signal_timeframe"] == "5m"
    assert r["signal_candle_high"] == 106.0
    assert r["signal_candle_low"] == 104.0
    assert r["signal_ema_value"] == 105.2
    assert r["signal_ema_slope"] == 0.05
    assert r["signal_atr"] == 1.2
    assert r["signal_bar_ts"] == 1_700_000_000_000
    assert r["max_age_seconds"] == 900
    assert r["max_bars"] == 3
    assert r["status"] == "pending"


def test_create_pending_signal_minimal_backward_compat(store):
    """Old callers using only the original 4 args must still work."""
    sig_id = store.create_pending_signal(
        symbol="SOL-USDT", action="buy",
        signal_price=100.0, timeout_minutes=30,
    )
    assert sig_id > 0
    rows = store.list_pending_signals()
    assert len(rows) == 1
    # Snapshot columns are absent/null — not persisted
    assert rows[0]["signal_candle_high"] is None


def test_invalidate_pending_signal_records_reason(store):
    sig_id = store.create_pending_signal(
        symbol="SOL-USDT", action="buy",
        signal_price=100.0, timeout_minutes=30,
    )
    store.invalidate_pending_signal(sig_id, reason="invalidated_structure_break")

    pending = store.list_pending_signals()
    assert pending == []  # no longer pending

    # Full row should be fetchable with status + reason preserved
    all_rows = store.list_all_signals()
    assert len(all_rows) == 1
    assert all_rows[0]["status"] == "invalidated"
    assert all_rows[0]["cancel_reason"] == "invalidated_structure_break"
