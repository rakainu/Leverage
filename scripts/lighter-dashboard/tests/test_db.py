import pytest

from lighter_dashboard.db import DashboardDB


def test_open_trades(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.open_trades()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SOL"
    assert rows[0]["side"] == "long"
    assert rows[0]["entry_price"] == 85.0


def test_closed_trades_newest_first(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.closed_trades(limit=10)
    assert [r["id"] for r in rows] == [2, 1]
    assert rows[0]["exit_reason"] == "sl"


def test_closed_pnls(fixture_db):
    db = DashboardDB(fixture_db)
    assert sorted(db.closed_pnls()) == [-35.0, 200.0]


def test_per_symbol_stats(fixture_db):
    db = DashboardDB(fixture_db)
    by = {r["symbol"]: r for r in db.per_symbol_stats()}
    assert by["ZEC"]["n"] == 2
    assert by["ZEC"]["net"] == 165.0


def test_exit_reason_mix(fixture_db):
    db = DashboardDB(fixture_db)
    mix = {r["exit_reason"]: r for r in db.exit_reason_mix()}
    assert mix["sl"]["n"] == 1
    assert mix["manual"]["n"] == 1


def test_signals(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.signals(limit=10)
    assert rows[0]["outcome"] == "fired"


def test_snapshots(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.snapshots()
    assert len(rows) == 2
    assert rows[-1]["portfolio_value"] == 2200


def test_query_only_blocks_writes(fixture_db):
    db = DashboardDB(fixture_db)
    with pytest.raises(Exception):
        with db._conn() as c:
            c.execute("INSERT INTO signal_log (symbol, side, bar_time) VALUES ('X','long','t')")
