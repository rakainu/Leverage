"""SQLite trade log. Schema mirrors the BloFin bridge for easy comparison."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    initial_sl      REAL,
    initial_tp      REAL,
    margin_usdt     REAL,
    leverage        REAL,
    base_amount     REAL,
    notional        REAL,
    exit_reason     TEXT,
    pnl_usdt        REAL,
    pnl_pct_account REAL,
    duration_secs   INTEGER,
    max_state       INTEGER,
    opened_at       TEXT,
    closed_at       TEXT,
    bar_time_open   TEXT,
    slope_pct       REAL,
    body_atr_ratio  REAL,
    adx_at_entry    REAL
);

CREATE TABLE IF NOT EXISTS signal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,
    bar_time        TEXT    NOT NULL,
    outcome         TEXT,           -- 'fired', 'blocked_filter', 'blocked_lock', 'expired'
    ema9            REAL,
    slope_pct       REAL,
    body_atr_ratio  REAL,
    detected_at     TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshot (
    ts              TEXT    NOT NULL,
    collateral      REAL,
    portfolio_value REAL,
    n_open          INTEGER,
    cum_pnl         REAL
);

-- Per-ticker entry switch. Only explicit overrides are stored; a symbol absent
-- from this table defaults to ENABLED (entries allowed). Persisted so a /off
-- survives a bridge restart.
CREATE TABLE IF NOT EXISTS ticker_switch (
    symbol          TEXT    PRIMARY KEY,
    entries_enabled INTEGER NOT NULL,
    updated_at      TEXT
);

-- Profit-withdrawal ledger. Each row is a realized skim of equity above target.
-- withdrawn_total = SUM(amount) reduces the equity base used for sizing + display,
-- mirroring a real transfer off the exchange. The bridge reads this to enforce
-- once-per-period cadence (survives restarts) and the dashboard shows the tally.
CREATE TABLE IF NOT EXISTS withdrawals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    amount        REAL    NOT NULL,
    equity_before REAL,
    equity_after  REAL,
    note          TEXT
);
"""


class TradeLogDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        # WAL mode: lock-free concurrent reads for the dashboard process.
        # Strategy-neutral — affects only write persistence, not trading logic.
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(SCHEMA)
        # Defensive migration for DBs created before initial_tp existed.
        try:
            self.conn.execute("ALTER TABLE trade_log ADD COLUMN initial_tp REAL")
        except sqlite3.OperationalError:
            pass  # column already exists
        self.conn.commit()
        log.info("DB ready at %s", self.path)

    def log_trade(self, **kwargs) -> int:
        cols = ",".join(kwargs.keys())
        placeholders = ",".join("?" for _ in kwargs)
        cur = self.conn.execute(
            f"INSERT INTO trade_log ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_trade_close(self, trade_id: int, **kwargs):
        sets = ",".join(f"{k}=?" for k in kwargs)
        self.conn.execute(
            f"UPDATE trade_log SET {sets} WHERE id=?",
            tuple(kwargs.values()) + (trade_id,),
        )
        self.conn.commit()

    def log_signal(self, **kwargs):
        cols = ",".join(kwargs.keys())
        placeholders = ",".join("?" for _ in kwargs)
        self.conn.execute(
            f"INSERT INTO signal_log ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        self.conn.commit()

    # ---- profit-withdrawal ledger ----
    def record_withdrawal(self, amount: float, equity_before: float,
                          equity_after: float, note: str = "") -> int:
        from datetime import datetime, timezone
        cur = self.conn.execute(
            "INSERT INTO withdrawals (ts, amount, equity_before, equity_after, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), amount, equity_before, equity_after, note),
        )
        self.conn.commit()
        return cur.lastrowid

    def withdrawn_total(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals").fetchone()
        return float(row[0] or 0.0)

    def last_withdrawal_ts(self) -> str | None:
        row = self.conn.execute(
            "SELECT ts FROM withdrawals ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def snapshot_account(self, collateral: float, portfolio_value: float,
                          n_open: int, cum_pnl: float):
        from datetime import datetime, timezone
        self.conn.execute(
            "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), collateral, portfolio_value, n_open, cum_pnl),
        )
        self.conn.commit()

    def get_open_trades(self) -> list[dict]:
        """Return all trades with closed_at IS NULL, used by startup restoration."""
        cur = self.conn.execute("""
            SELECT id, symbol, side, entry_price, margin_usdt, leverage,
                   base_amount, notional, opened_at, bar_time_open,
                   slope_pct, body_atr_ratio, initial_sl, initial_tp
            FROM trade_log
            WHERE closed_at IS NULL
            ORDER BY id
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def summary(self) -> dict:
        """Quick KPI summary for status checks."""
        c = self.conn.execute("""
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(pnl_usdt) AS net,
                SUM(CASE WHEN exit_reason='sl' THEN 1 ELSE 0 END) AS sl_hits,
                SUM(CASE WHEN exit_reason LIKE 'trail%' THEN 1 ELSE 0 END) AS trail_exits,
                SUM(CASE WHEN exit_reason='tp_ceiling' THEN 1 ELSE 0 END) AS ceiling_hits
            FROM trade_log
            WHERE pnl_usdt IS NOT NULL
        """).fetchone()
        n, wins, net, sl_hits, trail, ceiling = c
        return {
            "n_closed": n or 0,
            "wins": wins or 0,
            "win_rate": (wins / n) if n else 0,
            "net_pnl": net or 0,
            "sl_hits": sl_hits or 0,
            "trail_exits": trail or 0,
            "ceiling_hits": ceiling or 0,
        }

    # ----- per-ticker entry switch -----

    def get_switches(self) -> dict:
        """Return {symbol: bool} of explicit entry-switch overrides.
        Symbols absent here default to enabled (handled by the caller)."""
        cur = self.conn.execute("SELECT symbol, entries_enabled FROM ticker_switch")
        return {sym: bool(en) for sym, en in cur.fetchall()}

    def set_switch(self, symbol: str, enabled: bool):
        """Upsert a symbol's entry switch."""
        from datetime import datetime, timezone
        self.conn.execute(
            "INSERT INTO ticker_switch (symbol, entries_enabled, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET entries_enabled=excluded.entries_enabled, "
            "updated_at=excluded.updated_at",
            (symbol, 1 if enabled else 0, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
