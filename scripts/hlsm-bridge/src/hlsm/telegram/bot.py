"""Telegram bot for slash commands. Long-polling, no webhook required.

Commands:
- /hlsm status — current armed/paused state + key counters
- /hlsm pause — global pause
- /hlsm resume — clear pause + breaker + drain
- /hlsm drain — close all opens, halt new entries
- /hlsm pause <COIN> — per-coin pause
- /hlsm resume <COIN> — per-coin resume

Only authorized chat IDs (configured via env) may issue commands. Everything else gets ignored.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from hlsm.config import get_settings
from hlsm.db.session import get_session
from hlsm.safety import (
    apply_drain,
    apply_pause,
    apply_pause_coin,
    apply_resume,
    apply_resume_coin,
)
from hlsm.safety.state import get_safety_state

log = logging.getLogger(__name__)


class TelegramBot:
    """Wrapper around python-telegram-bot Application. Mostly slash-command routing."""

    def __init__(self, *, status_provider: Callable[[], dict] | None = None) -> None:
        self.settings = get_settings()
        self.status_provider = status_provider
        self._app: Application | None = None

    def _authorized(self, update: Update) -> bool:
        if not self.settings.telegram_chat_id:
            return False
        chat = update.effective_chat
        if chat is None:
            return False
        return str(chat.id) == str(self.settings.telegram_chat_id)

    async def cmd_hlsm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        msg = update.message
        if msg is None:
            return
        args = context.args or []
        if not args:
            await self._reply_status(update, context)
            return
        sub = args[0].lower()
        coin = args[1].upper() if len(args) > 1 else None
        if sub == "status":
            await self._reply_status(update, context)
        elif sub == "pause":
            if coin:
                with get_session() as sess:
                    apply_pause_coin(sess, coin)
                await msg.reply_text(f"⏸ paused {coin}")
            else:
                with get_session() as sess:
                    apply_pause(sess)
                await msg.reply_text("⏸ global pause — new entries halted")
        elif sub == "resume":
            if coin:
                with get_session() as sess:
                    apply_resume_coin(sess, coin)
                await msg.reply_text(f"▶ resumed {coin}")
            else:
                with get_session() as sess:
                    apply_resume(sess)
                await msg.reply_text("▶ ARMED — pause + drain + breaker cleared")
        elif sub == "drain":
            with get_session() as sess:
                apply_drain(sess)
            await msg.reply_text("🚰 drain mode — closing opens, halting entries")
        else:
            await msg.reply_text(
                "usage: /hlsm [status|pause|resume|drain] [COIN]"
            )

    async def _reply_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if msg is None:
            return
        with get_session() as sess:
            state = get_safety_state(sess)
        info = self.status_provider() if self.status_provider else {}
        armed = not state.paused_global and not state.drain_mode and not state.breaker_tripped
        flags = []
        if state.paused_global:
            flags.append("PAUSED")
        if state.drain_mode:
            flags.append("DRAIN")
        if state.breaker_tripped:
            flags.append("BREAKER")
        if state.paused_coins:
            flags.append(f"paused_coins={','.join(sorted(state.paused_coins))}")
        flags_text = " · ".join(flags) if flags else "ARMED"
        lines = [
            f"<b>HLSM</b> · <i>{flags_text}</i>",
            f"tracked_wallets: {info.get('tracked_wallets', '?')}",
            f"scored_wallets: {info.get('scored_wallets', '?')}",
            f"open_positions: {info.get('open_positions', '?')}",
            f"day_pnl_usdt: {info.get('day_pnl_usdt', '?')}",
        ]
        await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    async def _on_unknown(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG002
        return  # Silently ignore non-commands

    def build(self) -> Application:
        if self._app is not None:
            return self._app
        if not self.settings.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not configured")
        app = ApplicationBuilder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("hlsm", self.cmd_hlsm))
        app.add_handler(MessageHandler(filters.COMMAND, self._on_unknown))
        self._app = app
        return app

    async def run(self) -> None:
        app = self.build()
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        try:
            # Park forever
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
