"""Paper trading executor — logs trades without executing on-chain."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from config.settings import Settings
from db.database import get_db
from engine.signal import ConvergenceSignal
from engine.safety import SafetyResult
from executor.jupiter import JupiterClient

logger = logging.getLogger("smc.executor.paper")


class PaperExecutor:
    """Records paper trades and tracks price outcomes at 1h/4h/24h."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.jupiter = JupiterClient(settings.jupiter_api_key)

    async def execute(self, signal: ConvergenceSignal, safety: SafetyResult) -> int | None:
        """Record a paper trade. Returns position_id."""
        # Guard: don't open a duplicate position on a token we already hold
        db = await get_db()
        existing = await db.execute_fetchall(
            "SELECT id FROM positions WHERE token_mint=? AND status='open' LIMIT 1",
            (signal.token_mint,),
        )
        if existing:
            logger.info(
                f"Skipping duplicate paper trade — already have open position "
                f"#{existing[0]['id']} on {signal.token_mint[:12]}.."
            )
            return None

        entry_price = await self.jupiter.get_price_sol(signal.token_mint)
        if not entry_price:
            logger.warning(f"Could not get price for {signal.token_mint[:12]}.. — skipping paper trade")
            return None

        amount_tokens = self.settings.trade_amount_sol / entry_price

        cursor = await db.execute(
            """INSERT INTO positions
               (token_mint, token_symbol, mode, status, entry_price,
                amount_sol, amount_tokens, opened_at)
               VALUES (?, ?, 'paper', 'open', ?, ?, ?, ?)""",
            (
                signal.token_mint,
                signal.token_symbol,
                entry_price,
                self.settings.trade_amount_sol,
                amount_tokens,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        position_id = cursor.lastrowid

        logger.info(
            f"PAPER TRADE opened: {signal.token_mint[:12]}.. | "
            f"Entry: {entry_price:.12f} SOL | "
            f"Size: {self.settings.trade_amount_sol} SOL | "
            f"ID: {position_id}"
        )

        # Link signal to position
        await db.execute(
            "UPDATE convergence_signals SET position_id=?, action_taken='paper_trade' WHERE token_mint=? AND position_id IS NULL ORDER BY signal_at DESC LIMIT 1",
            (position_id, signal.token_mint),
        )
        await db.commit()

        return position_id

    async def snapshot_outcomes_loop(self):
        """Every 60 seconds, snapshot prices for open paper positions at 1h/4h/24h marks."""
        while True:
            await self._snapshot_once()
            await asyncio.sleep(60)

    async def _snapshot_once(self):
        """Check all open paper positions for milestone snapshots."""
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE status='open' AND mode='paper'"
        )

        for pos in rows:
            opened_at = datetime.fromisoformat(pos["opened_at"])
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            age_minutes = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

            current_price = await self.jupiter.get_price_sol(pos["token_mint"])
            if not current_price:
                continue

            pnl_pct = ((current_price - pos["entry_price"]) / pos["entry_price"]) * 100

            updates = {"current_price": current_price, "pnl_pct": pnl_pct}

            if age_minutes >= 60 and pos["price_1h"] is None:
                updates["price_1h"] = current_price
                updates["pnl_1h_pct"] = pnl_pct
                logger.info(f"Paper #{pos['id']} 1h snapshot: {pnl_pct:+.1f}%")

            if age_minutes >= 240 and pos["price_4h"] is None:
                updates["price_4h"] = current_price
                updates["pnl_4h_pct"] = pnl_pct
                logger.info(f"Paper #{pos['id']} 4h snapshot: {pnl_pct:+.1f}%")

            if age_minutes >= 1440 and pos["price_24h"] is None:
                updates["price_24h"] = current_price
                updates["pnl_24h_pct"] = pnl_pct
                updates["status"] = "closed"
                updates["close_reason"] = "24h_snapshot"
                updates["exit_price"] = current_price
                updates["closed_at"] = datetime.now(timezone.utc).isoformat()
                pnl_sol = (pnl_pct / 100) * pos["amount_sol"]
                updates["pnl_sol"] = pnl_sol
                logger.info(f"Paper #{pos['id']} 24h closed: {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL)")

            set_clause = ", ".join(f"{k}=?" for k in updates)
            values = list(updates.values()) + [pos["id"]]
            await db.execute(
                f"UPDATE positions SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                values,
            )
            await db.commit()
