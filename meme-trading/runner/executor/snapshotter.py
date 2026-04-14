"""MilestoneSnapshotter — captures price performance at fixed intervals."""
import asyncio
import json
from datetime import datetime, timezone

from runner.db.database import Database
from runner.utils.logging import get_logger

logger = get_logger("runner.executor.snapshotter")

MILESTONES = [
    (5 * 60,    "5m",  "price_5m_sol",  "pnl_5m_pct"),
    (30 * 60,   "30m", "price_30m_sol", "pnl_30m_pct"),
    (60 * 60,   "1h",  "price_1h_sol",  "pnl_1h_pct"),
    (4 * 3600,  "4h",  "price_4h_sol",  "pnl_4h_pct"),
    (24 * 3600, "24h", "price_24h_sol", "pnl_24h_pct"),
]


class MilestoneSnapshotter:
    def __init__(
        self,
        alert_bus,
        price_fetcher,
        db: Database,
        check_interval_sec: float = 30.0,
        error_closure_hours: float = 36.0,
        stop_loss_pct: float = 25.0,
        trail_activate_pct: float = 30.0,
        trail_distance_pct: float = 20.0,
        time_stop_sec: float = 14400.0,
        time_stop_pnl_max: float = 0.0,
    ):
        self.alert_bus = alert_bus
        self.price_fetcher = price_fetcher
        self.db = db
        self.check_interval_sec = check_interval_sec
        self.error_closure_hours = error_closure_hours
        self.stop_loss_pct = stop_loss_pct
        self.trail_activate_pct = trail_activate_pct
        self.trail_distance_pct = trail_distance_pct
        self.time_stop_sec = time_stop_sec
        self.time_stop_pnl_max = time_stop_pnl_max

    def _decide_exit(
        self, peak_pnl_pct: float, current_pnl_pct: float, elapsed_sec: float
    ) -> str | None:
        """Pure exit-decision logic. Returns close_reason or None."""
        if current_pnl_pct <= -abs(self.stop_loss_pct):
            return "stopped_out"
        if peak_pnl_pct >= self.trail_activate_pct:
            give_back = peak_pnl_pct - current_pnl_pct
            if give_back >= self.trail_distance_pct:
                return "trail_stop"
        if elapsed_sec >= self.time_stop_sec and current_pnl_pct < self.time_stop_pnl_max:
            return "time_stop"
        return None

    async def run(self) -> None:
        logger.info("milestone_snapshotter_start", interval=self.check_interval_sec)
        while True:
            await asyncio.sleep(self.check_interval_sec)
            try:
                await self._check_all()
            except Exception as e:
                logger.warning("snapshotter_cycle_error", error=str(e))

    async def _check_all(self) -> None:
        assert self.db.conn is not None
        async with self.db.conn.execute(
            """SELECT id, token_mint, symbol, runner_score_id, verdict, runner_score,
                      entry_price_sol, entry_price_usd, signal_time,
                      price_5m_sol, price_30m_sol, price_1h_sol, price_4h_sol, price_24h_sol,
                      max_favorable_pct, max_adverse_pct, amount_sol
               FROM paper_positions WHERE status = 'open'"""
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            pos = {
                "id": row[0], "token_mint": row[1], "symbol": row[2],
                "runner_score_id": row[3], "verdict": row[4], "runner_score": row[5],
                "entry_price_sol": row[6], "entry_price_usd": row[7], "signal_time": row[8],
                "price_5m_sol": row[9], "price_30m_sol": row[10],
                "price_1h_sol": row[11], "price_4h_sol": row[12], "price_24h_sol": row[13],
                "max_favorable_pct": row[14] or 0.0, "max_adverse_pct": row[15] or 0.0,
                "amount_sol": row[16],
            }
            await self._check_one(pos)

    async def _check_one(self, pos: dict) -> None:
        entry_price = pos["entry_price_sol"]
        if not entry_price or entry_price <= 0:
            logger.warning("skip_corrupted_entry_price", id=pos["id"], price=entry_price)
            return

        signal_time = datetime.fromisoformat(pos["signal_time"])
        if signal_time.tzinfo is None:
            signal_time = signal_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        elapsed_sec = (now - signal_time).total_seconds()

        error_threshold_sec = self.error_closure_hours * 3600
        if elapsed_sec > error_threshold_sec and pos["price_24h_sol"] is None:
            await self._error_close(pos)
            return

        price_data = await self.price_fetcher.fetch(pos["token_mint"])
        if price_data is None or not price_data.get("price_sol"):
            logger.debug("price_fetch_failed", mint=pos["token_mint"], id=pos["id"])
            return

        current_price = float(price_data["price_sol"])
        pnl_pct = (current_price - entry_price) / entry_price * 100.0

        assert self.db.conn is not None
        await self.db.conn.execute(
            """UPDATE paper_positions
               SET max_favorable_pct = MAX(max_favorable_pct, ?),
                   max_adverse_pct = MIN(max_adverse_pct, ?)
               WHERE id = ?""",
            (pnl_pct, pnl_pct, pos["id"]),
        )
        await self.db.conn.commit()

        # Exit policy: hard SL, trail-stop, time-stop. Runs every 30s.
        peak_pnl = max(float(pos["max_favorable_pct"] or 0.0), pnl_pct)
        exit_reason = self._decide_exit(peak_pnl, pnl_pct, elapsed_sec)
        if exit_reason:
            await self._exit_close(pos, current_price, pnl_pct, exit_reason)
            return

        wrote_24h = False
        existing = {
            "price_5m_sol": pos["price_5m_sol"], "price_30m_sol": pos["price_30m_sol"],
            "price_1h_sol": pos["price_1h_sol"], "price_4h_sol": pos["price_4h_sol"],
            "price_24h_sol": pos["price_24h_sol"],
        }
        for threshold_sec, label, price_col, pnl_col in MILESTONES:
            if elapsed_sec >= threshold_sec and existing.get(price_col) is None:
                await self.db.conn.execute(
                    f"UPDATE paper_positions SET {price_col} = ?, {pnl_col} = ? WHERE id = ? AND {price_col} IS NULL",
                    (current_price, pnl_pct, pos["id"]),
                )
                await self.db.conn.commit()
                logger.info("milestone_captured", id=pos["id"], label=label, pnl=round(pnl_pct, 2))
                if label == "24h":
                    wrote_24h = True

        if wrote_24h:
            await self._complete_close(pos, current_price, pnl_pct)

    async def _complete_close(self, pos, exit_price, final_pnl):
        assert self.db.conn is not None
        now = datetime.now(timezone.utc)
        await self.db.conn.execute(
            "UPDATE paper_positions SET status = 'closed', close_reason = 'completed', closed_at = ? WHERE id = ?",
            (now.isoformat(), pos["id"]),
        )
        await self.db.conn.commit()

        async with self.db.conn.execute(
            """SELECT pnl_5m_pct, pnl_30m_pct, pnl_1h_pct, pnl_4h_pct, pnl_24h_pct,
                      max_favorable_pct, max_adverse_pct FROM paper_positions WHERE id = ?""",
            (pos["id"],),
        ) as cur:
            row = await cur.fetchone()

        alert = {
            "type": "runner_close",
            "paper_position_id": pos["id"], "runner_score_id": pos["runner_score_id"],
            "token_mint": pos["token_mint"], "symbol": pos["symbol"],
            "verdict": pos["verdict"], "runner_score": pos["runner_score"],
            "entry_price_sol": pos["entry_price_sol"], "entry_price_usd": pos["entry_price_usd"],
            "exit_price_sol": exit_price,
            "milestones": {"5m": row[0], "30m": row[1], "1h": row[2], "4h": row[3], "24h": row[4]},
            "max_favorable_pct": row[5] or 0.0, "max_adverse_pct": row[6] or 0.0,
        }
        await self.alert_bus.put(alert)
        logger.info("paper_position_closed", id=pos["id"], pnl=round(final_pnl, 2))

    async def _exit_close(self, pos, exit_price, final_pnl, close_reason):
        """Close a position via the new exit policy (SL / trail / time)."""
        assert self.db.conn is not None
        now = datetime.now(timezone.utc)
        await self.db.conn.execute(
            "UPDATE paper_positions SET status = 'closed', close_reason = ?, "
            "closed_at = ? WHERE id = ?",
            (close_reason, now.isoformat(), pos["id"]),
        )
        await self.db.conn.commit()

        async with self.db.conn.execute(
            """SELECT pnl_5m_pct, pnl_30m_pct, pnl_1h_pct, pnl_4h_pct, pnl_24h_pct,
                      max_favorable_pct, max_adverse_pct FROM paper_positions WHERE id = ?""",
            (pos["id"],),
        ) as cur:
            row = await cur.fetchone()

        alert = {
            "type": "runner_close",
            "paper_position_id": pos["id"], "runner_score_id": pos["runner_score_id"],
            "token_mint": pos["token_mint"], "symbol": pos["symbol"],
            "verdict": pos["verdict"], "runner_score": pos["runner_score"],
            "entry_price_sol": pos["entry_price_sol"],
            "entry_price_usd": pos["entry_price_usd"],
            "exit_price_sol": exit_price,
            "close_reason": close_reason,
            "milestones": {"5m": row[0], "30m": row[1], "1h": row[2],
                           "4h": row[3], "24h": row[4]},
            "max_favorable_pct": row[5] or 0.0, "max_adverse_pct": row[6] or 0.0,
        }
        await self.alert_bus.put(alert)
        logger.info(
            "paper_position_exit",
            id=pos["id"], reason=close_reason, pnl=round(final_pnl, 2),
        )

    async def _error_close(self, pos):
        assert self.db.conn is not None
        now = datetime.now(timezone.utc)
        notes = json.dumps({"error_closure_reason": "persistent_price_failures"})
        await self.db.conn.execute(
            "UPDATE paper_positions SET status = 'closed', close_reason = 'error', closed_at = ?, notes_json = ? WHERE id = ?",
            (now.isoformat(), notes, pos["id"]),
        )
        await self.db.conn.commit()
        logger.warning("paper_position_error_closed", id=pos["id"], mint=pos["token_mint"])
