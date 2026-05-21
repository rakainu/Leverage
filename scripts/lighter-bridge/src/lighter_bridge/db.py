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
"""


class TradeLogDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.executescript(SCHEMA)
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

    def snapshot_account(self, collateral: float, portfolio_value: float,
                          n_open: int, cum_pnl: float):
        from datetime import datetime, timezone
        self.conn.execute(
            "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
            "VALUES (?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), collateral, portfolio_value, n_open, cum_pnl),
        )
        self.conn.commit()

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

    def close(self):
        self.conn.close()
