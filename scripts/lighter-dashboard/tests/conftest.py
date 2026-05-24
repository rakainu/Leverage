import sqlite3

import pytest

# Mirrors scripts/lighter-bridge/src/lighter_bridge/db.py SCHEMA.
SCHEMA = """
CREATE TABLE trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side TEXT NOT NULL,
    entry_price REAL, exit_price REAL, initial_sl REAL,
    margin_usdt REAL, leverage REAL, base_amount REAL, notional REAL,
    exit_reason TEXT, pnl_usdt REAL, pnl_pct_account REAL,
    duration_secs INTEGER, max_state INTEGER,
    opened_at TEXT, closed_at TEXT, bar_time_open TEXT,
    slope_pct REAL, body_atr_ratio REAL, adx_at_entry REAL
);
CREATE TABLE signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side TEXT NOT NULL, bar_time TEXT NOT NULL,
    outcome TEXT, ema9 REAL, slope_pct REAL, body_atr_ratio REAL, detected_at TEXT
);
CREATE TABLE account_snapshot (
    ts TEXT NOT NULL, collateral REAL, portfolio_value REAL,
    n_open INTEGER, cum_pnl REAL
);
"""


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / "lighter_paper.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    # Two closed trades (1 win, 1 loss) + one open trade.
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, exit_price, "
        "margin_usdt, leverage, base_amount, notional, exit_reason, pnl_usdt, "
        "max_state, opened_at, closed_at) VALUES "
        "(1,'ZEC','short',640.0,600.0,250,30,5.0,7500,'manual',200.0,2,"
        "'2026-05-22T15:30:00+00:00','2026-05-23T04:17:44+00:00')"
    )
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, exit_price, "
        "margin_usdt, leverage, base_amount, notional, exit_reason, pnl_usdt, "
        "max_state, opened_at, closed_at) VALUES "
        "(2,'ZEC','short',635.0,642.0,250,30,5.0,7500,'sl',-35.0,0,"
        "'2026-05-23T21:50:00+00:00','2026-05-23T22:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, "
        "margin_usdt, leverage, base_amount, notional, opened_at) VALUES "
        "(3,'SOL','long',85.0,250,30,88.0,7500,'2026-05-23T23:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO signal_log (symbol, side, bar_time, outcome, slope_pct, detected_at) "
        "VALUES ('ZEC','long','2026-05-23T22:00:00+00:00','fired',0.18,"
        "'2026-05-23T22:00:05+00:00')"
    )
    conn.execute(
        "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
        "VALUES ('2026-05-23T00:00:00+00:00',2000,2000,0,0)"
    )
    conn.execute(
        "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
        "VALUES ('2026-05-23T12:00:00+00:00',2200,2200,1,200)"
    )
    conn.commit()
    conn.close()
    return str(path)
