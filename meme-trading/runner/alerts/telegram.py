"""TelegramAlerter — sends formatted HTML alerts to Telegram."""
import asyncio

from runner.alerts.formatting import (
    format_close_alert,
    format_entry_alert,
    format_moonshot_alert,
)
from runner.utils.logging import get_logger

logger = get_logger("runner.alerts.telegram")

try:
    from telegram import Bot
except ImportError:
    Bot = None


class TelegramAlerter:
    def __init__(self, alert_bus: asyncio.Queue, bot_token: str, chat_id: str):
        self.alert_bus = alert_bus
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._enabled = bool(bot_token and chat_id)

    async def run(self) -> None:
        if not self._enabled:
            logger.info("telegram_disabled", reason="bot_token or chat_id not set")
        else:
            logger.info("telegram_alerter_start")
        while True:
            alert = await self.alert_bus.get()
            await self._process_one(alert)

    async def _process_one(self, alert: dict) -> None:
        alert_type = alert.get("type", "")
        if alert_type == "runner_entry":
            html = format_entry_alert(alert)
        elif alert_type == "runner_close":
            html = format_close_alert(alert)
        elif alert_type == "moonshot":
            html = format_moonshot_alert(alert)
        else:
            logger.debug("unknown_alert_type", alert_type=alert_type)
            return
        if not self._enabled:
            return
        try:
            bot = Bot(token=self.bot_token)
            await bot.send_message(chat_id=self.chat_id, text=html, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            logger.warning("telegram_send_failed", error=str(e), alert_type=alert_type)
