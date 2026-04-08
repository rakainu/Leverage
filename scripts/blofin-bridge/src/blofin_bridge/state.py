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
    sl_policy: str
    opened_at: str
    closed_at: Optional[str]
    realized_pnl: Optional[float]
    source: Optional[str]


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA_FILE.read_text())

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
            sl_order_id=row["sl_order_id"], sl_policy=row["sl_policy"],
            opened_at=row["opened_at"], closed_at=row["closed_at"],
            realized_pnl=row["realized_pnl"], source=row["source"],
        )
