"""Position manager — monitors open positions for TP/SL/timeout exits."""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import Settings
from db.database import get_db
from executor.jupiter import JupiterClient

logger = logging.getLogger("smc.executor.positions")


class PositionManager:
    """Every 15 seconds, checks all open positions for exit conditions."""

    def __init__(self, settings: Settings, alert_bus: asyncio.Queue):
        self.settings = settings
        self.alert_bus = alert_bus
        self.jupiter = JupiterClient(settings.jupiter_api_key)

    async def run(self):
        """Main monitoring loop."""
        logger.info("Position manager started")
        while True:
            try:
                await self._check_positions()
            except Exception as e:
                logger.error(f"Position manager error: {e}")
            await asyncio.sleep(15)

    async def _check_positions(self):
        """Evaluate all open positions."""
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE status='open'"
        )

        for pos in rows:
            await self._evaluate_position(pos, db)

    async def _evaluate_position(self, pos, db):
        """Check a single position for exit conditions."""
        current_price = await self.jupiter.get_price_sol(pos["token_mint"])
        if not current_price:
            return

        entry_price = pos["entry_price"]
        pnl_pct = ((current_price - entry_price) / entry_price) * 100

        opened_at = datetime.fromisoformat(pos["opened_at"])
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

        close_reason = None
        if pnl_pct >= self.settings.take_profit_pct:
            close_reason = "take_profit"
        elif pnl_pct <= -self.settings.stop_loss_pct:
            close_reason = "stop_loss"
        elif age_min >= self.settings.position_timeout_minutes:
            close_reason = "timeout"

        if close_reason:
            pnl_sol = (pnl_pct / 100) * pos["amount_sol"]
            now = datetime.now(timezone.utc).isoformat()

            await db.execute(
                """UPDATE positions SET
                   status='closed', close_reason=?, exit_price=?,
                   current_price=?, pnl_pct=?, pnl_sol=?,
                   closed_at=?, updated_at=?
                   WHERE id=?""",
                (close_reason, current_price, current_price,
                 pnl_pct, pnl_sol, now, now, pos["id"]),
            )
            await db.commit()

            logger.info(
                f"Position #{pos['id']} CLOSED ({close_reason}): "
                f"{pos['token_mint'][:12]}.. | "
                f"P&L: {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL)"
            )

            # Push alert
            await self.alert_bus.put({
                "type": "position_closed",
                "position_id": pos["id"],
                "token_mint": pos["token_mint"],
                "token_symbol": pos["token_symbol"],
                "reason": close_reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_sol": round(pnl_sol, 4),
                "mode": pos["mode"],
            })
        else:
            # Just update current price
            await db.execute(
                "UPDATE positions SET current_price=?, pnl_pct=?, updated_at=? WHERE id=?",
                (current_price, pnl_pct, datetime.now(timezone.utc).isoformat(), pos["id"]),
            )
            await db.commit()
