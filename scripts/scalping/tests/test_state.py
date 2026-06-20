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


def test_create_pending_signal_defaults_source_pro_v3(store):
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=300.0,
    )
    sigs = store.list_pending_signals()
    assert len(sigs) == 1
    assert sigs[0]["source"] == "pro_v3"


def test_create_pending_signal_records_explicit_source(store):
    store.create_pending_signal(
        symbol="ZEC-USDT", action="sell", signal_price=500.0, source="ha_v3",
    )
    assert store.list_pending_signals()[0]["source"] == "ha_v3"


def test_pending_signals_source_column_added_to_legacy_db(tmp_path):
    """A pre-source DB (pending_signals with no `source` column) must be
    migrated in-place when the Store opens it — not crash, and legacy rows
    backfilled to the 'pro_v3' default."""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE pending_signals (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT NOT NULL,
            action       TEXT NOT NULL,
            signal_price REAL NOT NULL,
            created_at   TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'pending',
            filled_at    TEXT,
            fill_price   REAL
        );
        INSERT INTO pending_signals
            (symbol, action, signal_price, created_at, expires_at, status)
        VALUES ('SOL-USDT', 'buy', 300.0,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:30:00+00:00', 'pending');
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)  # opening must migrate, not raise
    sigs = store.list_pending_signals()
    assert len(sigs) == 1
    assert sigs[0]["source"] == "pro_v3"


def _open_long(store, entry_price=300.0):
    return store.create_position(
        symbol="SOL-USDT", side="long", entry_price=entry_price,
        initial_size=10, sl_policy="p2_step_stop", source="ha_v3",
        margin_usdt=100.0, leverage=30.0,
    )


def test_log_trade_records_fee_and_gross_pnl(store):
    """pnl_usdt is GROSS (from the real exit price); fee_usdt is stored
    separately so net = gross + fee is derivable. Gross never depends on fees."""
    pid = _open_long(store, entry_price=300.0)
    # long, exit below entry: gross = (297/300 - 1) * (100*30 notional) = -30
    tid = store.log_trade(
        position_id=pid, exit_price=297.0, exit_reason="sl",
        margin_usdt=100.0, leverage=30.0, initial_sl=297.0, tp_ceiling=None,
        fee_usdt=-9.0,
    )
    assert tid > 0
    row = store.get_trade_log(limit=1)[0]
    assert row["pnl_usdt"] == pytest.approx(-30.0)   # gross, from exit price
    assert row["fee_usdt"] == pytest.approx(-9.0)


def test_log_trade_fee_defaults_zero(store):
    """Zero-fee venue / caller omits fee → fee_usdt stored as 0.0, not NULL-crash."""
    pid = _open_long(store, entry_price=300.0)
    store.log_trade(
        position_id=pid, exit_price=303.0, exit_reason="trail_sl",
        margin_usdt=100.0, leverage=30.0, initial_sl=None, tp_ceiling=None,
    )
    row = store.get_trade_log(limit=1)[0]
    assert row["pnl_usdt"] == pytest.approx(30.0)   # (303/300-1)*3000 gross
    assert row["fee_usdt"] == 0.0


def test_trade_log_fee_column_added_to_legacy_db(tmp_path):
    """A pre-fee trade_log (no fee_usdt column) is migrated in-place on open."""
    import sqlite3
    db = tmp_path / "legacy_fee.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER, symbol TEXT NOT NULL, side TEXT NOT NULL,
            entry_price REAL NOT NULL, exit_price REAL,
            margin_usdt REAL NOT NULL, leverage REAL NOT NULL,
            initial_sl REAL, tp_ceiling REAL,
            trail_activated INTEGER NOT NULL DEFAULT 0, trail_high_price REAL,
            exit_reason TEXT, pnl_usdt REAL, pnl_pct REAL,
            opened_at TEXT NOT NULL, closed_at TEXT NOT NULL, duration_secs INTEGER
        );
        INSERT INTO trade_log
            (position_id, symbol, side, entry_price, exit_price, margin_usdt,
             leverage, exit_reason, pnl_usdt, opened_at, closed_at)
        VALUES (1, 'SOL-USDT', 'long', 300.0, 297.0, 100.0, 30.0, 'sl', -300.0,
                '2026-01-01T00:00:00+00:00', '2026-01-01T00:10:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)  # opening must migrate, not raise
    rows = store.get_trade_log(limit=10)
    assert len(rows) == 1
    assert rows[0]["fee_usdt"] == 0.0   # legacy row backfilled to 0


def test_record_and_clear_pending_limit(store):
    """A pending signal tracks its resting limit-entry order id + price so the
    poller can refresh/cancel it and detect the fill."""
    sid = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=80.0, source="ha_v3",
    )
    store.record_pending_limit(sid, order_id="lim-1", price=79.95)
    row = [s for s in store.list_pending_signals() if s["id"] == sid][0]
    assert row["limit_order_id"] == "lim-1"
    assert row["limit_price"] == pytest.approx(79.95)

    store.clear_pending_limit(sid)
    row = [s for s in store.list_pending_signals() if s["id"] == sid][0]
    assert row["limit_order_id"] is None
    assert row["limit_price"] is None


def test_pending_limit_columns_added_to_legacy_db(tmp_path):
    """A pending_signals table without the limit columns migrates in-place."""
    import sqlite3
    db = tmp_path / "legacy_lim.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE pending_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
            action TEXT NOT NULL, signal_price REAL NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', filled_at TEXT, fill_price REAL
        );
        INSERT INTO pending_signals (symbol, action, signal_price, created_at, expires_at)
        VALUES ('SOL-USDT','buy',80.0,'2026-01-01T00:00:00+00:00','2026-01-01T00:30:00+00:00');
        """
    )
    conn.commit(); conn.close()
    store = Store(db)  # must migrate, not raise
    row = store.list_pending_signals()[0]
    assert row["limit_order_id"] is None
    assert row["limit_price"] is None


def test_append_event_and_update_outcome(store):
    eid = store.append_event(
        position_id=None, event_type="buy",
        payload='{"action":"buy"}',
    )
    store.mark_event_handled(eid, outcome="ok", error_msg=None)
    events = store.recent_events(limit=10)
    assert len(events) == 1
    assert events[0]["outcome"] == "ok"
