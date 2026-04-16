"""SQLite-backed position & event store."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

SCHEMA_FILE = Path(__file__).parent / "db" / "schema.sql"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PositionRow:
    id: int
    symbol: str
    side: str
    entry_price: float
    initial_size: float
    current_size: float
    tp_stage: int
    tp1_fill_price: Optional[float]
    tp2_fill_price: Optional[float]
    sl_order_id: Optional[str]
    tp1_order_id: Optional[str]
    tp2_order_id: Optional[str]
    tp3_order_id: Optional[str]
    sl_distance: Optional[float]
    atr_value: Optional[float]
    trail_high_price: Optional[float]
    trail_active: int
    sl_policy: str
    opened_at: str
    closed_at: Optional[str]
    realized_pnl: Optional[float]
    source: Optional[str]


_PENDING_SNAPSHOT_COLUMNS = (
    ("signal_timeframe", "TEXT"),
    ("signal_candle_high", "REAL"),
    ("signal_candle_low", "REAL"),
    ("signal_ema_value", "REAL"),
    ("signal_ema_slope", "REAL"),
    ("signal_atr", "REAL"),
    ("signal_bar_ts", "INTEGER"),
    ("max_age_seconds", "INTEGER"),
    ("max_bars", "INTEGER"),
    ("cancel_reason", "TEXT"),
)


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA_FILE.read_text())
            self._migrate_pending_snapshot_columns(c)

    @staticmethod
    def _migrate_pending_snapshot_columns(c: sqlite3.Connection) -> None:
        """Idempotent ALTER TABLE migration for pre-snapshot DBs."""
        existing = {
            row["name"]
            for row in c.execute("PRAGMA table_info(pending_signals)").fetchall()
        }
        for name, sqltype in _PENDING_SNAPSHOT_COLUMNS:
            if name not in existing:
                c.execute(f"ALTER TABLE pending_signals ADD COLUMN {name} {sqltype}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # -------- positions --------

    def create_position(
        self, *, symbol: str, side: str, entry_price: float,
        initial_size: float, sl_policy: str, source: str,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO positions
                  (symbol, side, entry_price, initial_size, current_size,
                   tp_stage, sl_policy, opened_at, source)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (symbol, side, entry_price, initial_size, initial_size,
                 sl_policy, _now_iso(), source),
            )
            return cur.lastrowid

    def get_position(self, pid: int) -> Optional[PositionRow]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM positions WHERE id = ?", (pid,)
            ).fetchone()
        return self._row_to_position(row) if row else None

    def get_open_position(self, symbol: str) -> Optional[PositionRow]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM positions WHERE symbol = ? AND closed_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return self._row_to_position(row) if row else None

    def record_tp_fill(
        self, pid: int, *, stage: int, fill_price: float, closed_contracts: float,
    ) -> None:
        col = "tp1_fill_price" if stage == 1 else "tp2_fill_price" if stage == 2 else None
        with self._conn() as c:
            if col:
                c.execute(
                    f"UPDATE positions SET tp_stage = ?, {col} = ?, "
                    f"current_size = current_size - ? WHERE id = ?",
                    (stage, fill_price, closed_contracts, pid),
                )
            else:
                c.execute(
                    "UPDATE positions SET tp_stage = ?, "
                    "current_size = current_size - ? WHERE id = ?",
                    (stage, closed_contracts, pid),
                )

    def record_sl_order_id(self, pid: int, order_id: Optional[str]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET sl_order_id = ? WHERE id = ?",
                (order_id, pid),
            )

    def record_tp_order_ids(
        self, pid: int, *,
        tp1_order_id: Optional[str], tp2_order_id: Optional[str],
        tp3_order_id: Optional[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET tp1_order_id = ?, tp2_order_id = ?, "
                "tp3_order_id = ? WHERE id = ?",
                (tp1_order_id, tp2_order_id, tp3_order_id, pid),
            )

    def record_atr_context(
        self, pid: int, *, atr_value: float, sl_distance: float,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET atr_value = ?, sl_distance = ? WHERE id = ?",
                (atr_value, sl_distance, pid),
            )

    def update_trail(
        self, pid: int, *, trail_high_price: float, trail_active: int,
    ) -> None:
        """trail_active: 0=inactive, 1=SL jumped (locked), 2=trailing."""
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET trail_high_price = ?, trail_active = ? WHERE id = ?",
                (trail_high_price, trail_active, pid),
            )

    def clear_tp_order_id(self, pid: int, stage: int) -> None:
        col = {1: "tp1_order_id", 2: "tp2_order_id", 3: "tp3_order_id"}.get(stage)
        if col is None:
            raise ValueError(f"invalid tp stage {stage}")
        with self._conn() as c:
            c.execute(f"UPDATE positions SET {col} = NULL WHERE id = ?", (pid,))

    def close_position(self, pid: int, *, realized_pnl: Optional[float]) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET closed_at = ?, realized_pnl = ? WHERE id = ?",
                (_now_iso(), realized_pnl, pid),
            )

    def list_open_positions(self) -> list[PositionRow]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions WHERE closed_at IS NULL"
            ).fetchall()
        return [self._row_to_position(r) for r in rows]

    # -------- trade log --------

    def log_trade(
        self, *, position_id: int, exit_price: Optional[float],
        exit_reason: str, margin_usdt: float, leverage: float,
        initial_sl: Optional[float], tp_ceiling: Optional[float],
    ) -> int:
        """Record a completed trade for research/analysis."""
        pos = self.get_position(position_id)
        if pos is None:
            return -1

        closed_at = _now_iso()
        pnl_usdt: Optional[float] = None
        pnl_pct: Optional[float] = None
        duration_secs: Optional[int] = None

        if exit_price is not None and pos.entry_price:
            notional = margin_usdt * leverage
            if pos.side == "long":
                pnl_usdt = ((exit_price - pos.entry_price) / pos.entry_price) * notional
            else:
                pnl_usdt = ((pos.entry_price - exit_price) / pos.entry_price) * notional
            pnl_pct = (pnl_usdt / margin_usdt) * 100 if margin_usdt else None

        try:
            from datetime import datetime
            opened = datetime.fromisoformat(pos.opened_at)
            closed = datetime.fromisoformat(closed_at)
            duration_secs = int((closed - opened).total_seconds())
        except Exception:
            pass

        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO trade_log
                  (position_id, symbol, side, entry_price, exit_price,
                   margin_usdt, leverage, initial_sl, tp_ceiling,
                   trail_activated, trail_high_price, exit_reason,
                   pnl_usdt, pnl_pct, opened_at, closed_at, duration_secs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (position_id, pos.symbol, pos.side, pos.entry_price,
                 exit_price, margin_usdt, leverage, initial_sl, tp_ceiling,
                 pos.trail_active, pos.trail_high_price, exit_reason,
                 pnl_usdt, pnl_pct, pos.opened_at, closed_at, duration_secs),
            )
            return cur.lastrowid

    def get_trade_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM trade_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -------- pending signals --------

    def create_pending_signal(
        self, *, symbol: str, action: str, signal_price: float,
        timeout_minutes: int = 30,
        # --- snapshot fields (all optional for backward-compat) ---
        signal_timeframe: Optional[str] = None,
        signal_candle_high: Optional[float] = None,
        signal_candle_low: Optional[float] = None,
        signal_ema_value: Optional[float] = None,
        signal_ema_slope: Optional[float] = None,
        signal_atr: Optional[float] = None,
        signal_bar_ts: Optional[int] = None,
        max_age_seconds: Optional[int] = None,
        max_bars: Optional[int] = None,
    ) -> int:
        import datetime as _dt
        now = datetime.now(timezone.utc)
        expires = now + _dt.timedelta(minutes=timeout_minutes)
        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO pending_signals
                  (symbol, action, signal_price, created_at, expires_at, status,
                   signal_timeframe, signal_candle_high, signal_candle_low,
                   signal_ema_value, signal_ema_slope, signal_atr,
                   signal_bar_ts, max_age_seconds, max_bars)
                VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol, action, signal_price,
                    now.isoformat(), expires.isoformat(),
                    signal_timeframe, signal_candle_high, signal_candle_low,
                    signal_ema_value, signal_ema_slope, signal_atr,
                    signal_bar_ts, max_age_seconds, max_bars,
                ),
            )
            return cur.lastrowid

    def list_pending_signals(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM pending_signals WHERE status = 'pending'"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all_signals(self, limit: int = 200) -> list[dict[str, Any]]:
        """All signals regardless of status — for audit/debugging."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM pending_signals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def invalidate_pending_signal(self, sig_id: int, *, reason: str) -> None:
        """Mark a pending signal invalidated with a reason code."""
        with self._conn() as c:
            c.execute(
                "UPDATE pending_signals SET status = 'invalidated', "
                "cancel_reason = ? WHERE id = ?",
                (reason, sig_id),
            )

    def fill_pending_signal(self, sig_id: int, fill_price: float) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE pending_signals SET status = 'filled', filled_at = ?, "
                "fill_price = ? WHERE id = ?",
                (_now_iso(), fill_price, sig_id),
            )

    def expire_pending_signal(self, sig_id: int, *, reason: str = "expired_time_limit") -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE pending_signals SET status = 'expired', cancel_reason = ? "
                "WHERE id = ?",
                (reason, sig_id),
            )

    def cancel_pending_signals_for_symbol(self, symbol: str) -> int:
        """Cancel all pending signals for a symbol (e.g. on reversal or new signal)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE pending_signals SET status = 'cancelled' "
                "WHERE symbol = ? AND status = 'pending'",
                (symbol,),
            )
            return cur.rowcount

    # -------- events --------

    def append_event(
        self, *, position_id: Optional[int], event_type: str, payload: str,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (position_id, event_type, payload, received_at) "
                "VALUES (?, ?, ?, ?)",
                (position_id, event_type, payload, _now_iso()),
            )
            return cur.lastrowid

    def mark_event_handled(
        self, eid: int, *, outcome: str, error_msg: Optional[str],
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE events SET handled_at = ?, outcome = ?, error_msg = ? "
                "WHERE id = ?",
                (_now_iso(), outcome, error_msg, eid),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> PositionRow:
        return PositionRow(
            id=row["id"], symbol=row["symbol"], side=row["side"],
            entry_price=row["entry_price"], initial_size=row["initial_size"],
            current_size=row["current_size"], tp_stage=row["tp_stage"],
            tp1_fill_price=row["tp1_fill_price"],
            tp2_fill_price=row["tp2_fill_price"],
            sl_order_id=row["sl_order_id"],
            tp1_order_id=row["tp1_order_id"],
            tp2_order_id=row["tp2_order_id"],
            tp3_order_id=row["tp3_order_id"],
            sl_distance=row["sl_distance"],
            atr_value=row["atr_value"],
            trail_high_price=row["trail_high_price"],
            trail_active=row["trail_active"],
            sl_policy=row["sl_policy"],
            opened_at=row["opened_at"], closed_at=row["closed_at"],
            realized_pnl=row["realized_pnl"], source=row["source"],
        )
