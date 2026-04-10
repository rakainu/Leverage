"""Position manager — monitors open positions with trailing stop loss."""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import Settings
from db.database import get_db
from executor.jupiter import JupiterClient

logger = logging.getLogger("smc.executor.positions")


class PositionManager:
    """Every 5 seconds, checks all open positions for exit conditions.

    Exit logic (trailing stop):
      Phase 1 (entry):  SL at -stop_loss_pct (default -25%)
      Phase 2 (profit): Once pnl >= trail_activate_pct (default +30%),
                         move SL floor to +trail_breakeven_pct (default +5%)
                         and trail at trail_distance_pct below high watermark.
      Phase 3 (peak):   As price rises, SL ratchets up. Never moves down.
      Timeout:           Close after position_timeout_minutes regardless.
    """

    def __init__(self, settings: Settings, alert_bus: asyncio.Queue):
        self.settings = settings
        self.alert_bus = alert_bus
        self.jupiter = JupiterClient(settings.jupiter_api_key)

    async def run(self):
        """Main monitoring loop — 5s interval for tighter SL execution."""
        logger.info(
            f"Position manager started (5s interval, "
            f"trail: activate@+{self.settings.trail_activate_pct}%, "
            f"lock@+{self.settings.trail_breakeven_pct}%, "
            f"distance {self.settings.trail_distance_pct}%)"
        )
        while True:
            try:
                await self._check_positions()
            except Exception as e:
                logger.error(f"Position manager error: {e}")
            await asyncio.sleep(5)

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
        if not entry_price or entry_price == 0:
            return

        pnl_pct = ((current_price - entry_price) / entry_price) * 100

        # Cap at +/-1000% — anything beyond is bad price data
        if abs(pnl_pct) > 1000:
            logger.warning(
                f"Position #{pos['id']} P&L {pnl_pct:+.0f}% exceeds cap, "
                f"entry={entry_price}, current={current_price} — skipping"
            )
            return

        opened_at = datetime.fromisoformat(pos["opened_at"])
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60

        # --- Update high watermark ---
        prev_hwm = pos["high_watermark_pct"] or 0.0
        hwm = max(prev_hwm, pnl_pct)

        # --- Compute dynamic stop level ---
        stop_level = self._compute_stop_level(hwm)

        # --- Check exit conditions ---
        close_reason = None
        if pnl_pct <= stop_level:
            if hwm >= self.settings.trail_activate_pct:
                close_reason = "trailing_stop"
            else:
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
                   high_watermark_pct=?,
                   closed_at=?, updated_at=?
                   WHERE id=?""",
                (close_reason, current_price, current_price,
                 pnl_pct, pnl_sol, hwm, now, now, pos["id"]),
            )
            await db.commit()

            logger.info(
                f"Position #{pos['id']} CLOSED ({close_reason}): "
                f"{pos['token_mint'][:12]}.. | "
                f"P&L: {pnl_pct:+.1f}% ({pnl_sol:+.4f} SOL) | "
                f"HWM: {hwm:+.1f}%"
            )

            await self.alert_bus.put({
                "type": "position_closed",
                "position_id": pos["id"],
                "token_mint": pos["token_mint"],
                "token_symbol": pos["token_symbol"],
                "reason": close_reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_sol": round(pnl_sol, 4),
                "high_watermark_pct": round(hwm, 2),
                "mode": pos["mode"],
            })
        else:
            # Update current price and high watermark
            await db.execute(
                """UPDATE positions SET
                   current_price=?, pnl_pct=?, high_watermark_pct=?, updated_at=?
                   WHERE id=?""",
                (current_price, pnl_pct, hwm,
                 datetime.now(timezone.utc).isoformat(), pos["id"]),
            )
            await db.commit()

    def _compute_stop_level(self, high_watermark_pct: float) -> float:
        """Compute the current stop-loss level based on trailing logic.

        Returns the P&L% at which the position should be stopped out.

        Phase 1: HWM < trail_activate_pct -> fixed SL at -stop_loss_pct
        Phase 2: HWM >= trail_activate_pct -> max(trail_breakeven_pct, HWM - trail_distance_pct)
        """
        if high_watermark_pct < self.settings.trail_activate_pct:
            return -self.settings.stop_loss_pct

        # Trailing active: SL is the higher of breakeven lock or trailing distance
        trailing_level = high_watermark_pct - self.settings.trail_distance_pct
        return max(self.settings.trail_breakeven_pct, trailing_level)
