"""Telegram push notifications for SMC trading events."""

import asyncio
import json
import logging

from config.settings import Settings

logger = logging.getLogger("smc.alerts.telegram")


class TelegramAlerter:
    """Sends formatted alerts to Telegram when trading events occur."""

    def __init__(self, settings: Settings):
        self.bot_token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.enabled = bool(self.bot_token)
        self._bot = None

    async def _get_bot(self):
        """Lazy-init the telegram bot."""
        if self._bot is None and self.enabled:
            from telegram import Bot
            self._bot = Bot(token=self.bot_token)
        return self._bot

    async def run(self, alert_bus: asyncio.Queue):
        """Consume alerts and send to Telegram."""
        if not self.enabled:
            logger.warning("Telegram bot token not set — alerts disabled")
            while True:
                await alert_bus.get()  # Drain queue silently
        else:
            logger.info("Telegram alerter started")

        while True:
            alert = await alert_bus.get()
            try:
                msg = self._format(alert)
                bot = await self._get_bot()
                if bot:
                    await bot.send_message(
                        chat_id=self.chat_id,
                        text=msg,
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.error(f"Telegram send failed: {e}")

    def _format(self, alert: dict) -> str:
        """Format an alert dict into a Telegram HTML message."""
        t = alert.get("type", "unknown")

        if t == "convergence_signal":
            safety = "PASS" if alert.get("safety_passed") else "FAIL"
            reasons = alert.get("safety_reasons", [])
            reason_text = f"\nReasons: {', '.join(reasons)}" if reasons else ""
            return (
                f"<b>CONVERGENCE SIGNAL</b>\n"
                f"Token: <code>{alert.get('token_symbol') or alert.get('token_mint', '?')[:16]}</code>\n"
                f"Wallets: {alert.get('wallet_count', '?')}\n"
                f"Total SOL: {alert.get('total_amount_sol', 0):.2f}\n"
                f"Safety: {safety}{reason_text}"
            )

        elif t == "position_opened":
            mode = alert.get("mode", "?").upper()
            return (
                f"<b>{mode} POSITION OPENED</b>\n"
                f"Token: <code>{alert.get('token_symbol') or alert.get('token_mint', '?')[:16]}</code>\n"
                f"Size: {alert.get('amount_sol', 0):.3f} SOL"
            )

        elif t == "position_closed":
            pnl = alert.get("pnl_pct", 0)
            pnl_sol = alert.get("pnl_sol", 0)
            prefix = "+" if pnl > 0 else ""
            return (
                f"<b>POSITION CLOSED</b> ({alert.get('reason', '?')})\n"
                f"Token: <code>{alert.get('token_symbol') or alert.get('token_mint', '?')[:16]}</code>\n"
                f"P&L: {prefix}{pnl:.1f}% ({prefix}{pnl_sol:.4f} SOL)\n"
                f"Mode: {alert.get('mode', '?')}"
            )

        elif t == "safety_failed":
            reasons = alert.get("reasons", [])
            return (
                f"<b>SAFETY FAILED</b>\n"
                f"Token: <code>{alert.get('token_mint', '?')[:16]}</code>\n"
                f"Reasons: {', '.join(reasons)}"
            )

        return f"<b>SMC</b>: {json.dumps(alert, default=str)}"
