"""Read-only query layer over the bridge's SQLite DB.

Every connection sets PRAGMA query_only=ON so the dashboard can never
mutate trade data, even though WAL requires a writable directory mount.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class DashboardDB:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def open_trades(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, symbol, side, entry_price, base_amount, margin_usdt, "
                "leverage, notional, opened_at FROM trade_log "
                "WHERE closed_at IS NULL ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_trades(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, symbol, side, entry_price, exit_price, exit_reason, "
                "pnl_usdt, max_state, opened_at, closed_at FROM trade_log "
                "WHERE closed_at IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_pnls(self) -> list[float]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT pnl_usdt FROM trade_log WHERE pnl_usdt IS NOT NULL"
            ).fetchall()
        return [float(r["pnl_usdt"]) for r in rows]

    def per_symbol_stats(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, COUNT(*) AS n, "
                "SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins, "
                "ROUND(SUM(pnl_usdt), 2) AS net "
                "FROM trade_log WHERE pnl_usdt IS NOT NULL "
                "GROUP BY symbol ORDER BY symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    def exit_reason_mix(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT exit_reason, COUNT(*) AS n, ROUND(SUM(pnl_usdt), 2) AS net "
                "FROM trade_log WHERE exit_reason IS NOT NULL "
                "GROUP BY exit_reason ORDER BY n DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def signals(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, side, bar_time, outcome, slope_pct, detected_at "
                "FROM signal_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def snapshots(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, collateral, portfolio_value, n_open, cum_pnl "
                "FROM account_snapshot ORDER BY ts"
            ).fetchall()
        return [dict(r) for r in rows]
