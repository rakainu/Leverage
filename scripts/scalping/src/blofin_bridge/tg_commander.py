"""Telegram bot command listener for Fin (BloFin scalping bridge).

Long-polls `getUpdates` and dispatches slash commands + inline keyboard
callbacks to pause/resume per-symbol entries via EntryGate. Auth is
restricted to a single allowed Telegram user ID — unauthorized updates
are silently dropped so the bot's existence is not leaked.

Runs as an asyncio background task alongside PositionPoller inside the
scalping container's FastAPI lifespan. Does not touch the trade execution
path — only reads/mutates EntryGate state and cancels pending signals.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

import httpx

from .entry_gate import EntryGate
from .state import Store

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT = 25  # seconds — Telegram's max
POLL_BACKOFF_INITIAL = 5
POLL_BACKOFF_MAX = 60


def _build_alias_map(symbols: list[str]) -> dict[str, str]:
    """Map 'sol' / 'SOL' / 'SOL-USDT' → 'SOL-USDT'."""
    out: dict[str, str] = {}
    for sym in symbols:
        out[sym.lower()] = sym
        base = sym.split("-")[0].lower()
        out[base] = sym
    return out


class TelegramCommander:
    """Async bot: listens for /menu /stop /start /status + button taps."""

    def __init__(
        self,
        *,
        bot_token: str,
        allowed_user_id: int,
        gate: EntryGate,
        store: Store,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_id = allowed_user_id
        self.gate = gate
        self.store = store
        self._aliases = _build_alias_map(sorted(gate.status().keys()))
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._offset: int = 0
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token) and self.allowed_user_id > 0

    # -------- lifecycle --------

    def start(self) -> None:
        if self._task is not None:
            return
        if not self.enabled:
            log.info("TelegramCommander disabled (no bot token or user id)")
            return
        self._stop_event = asyncio.Event()
        self._client = httpx.AsyncClient()
        self._task = asyncio.create_task(
            self._poll_loop(), name="tg-commander-loop"
        )
        log.info("TelegramCommander started")

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._task = None
        self._stop_event = None
        log.info("TelegramCommander stopped")

    async def _poll_loop(self) -> None:
        backoff = POLL_BACKOFF_INITIAL
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                updates = await self._get_updates()
                for update in updates:
                    self._offset = max(self._offset, update.get("update_id", 0) + 1)
                    try:
                        await self._handle_update(update)
                    except Exception:
                        log.exception(
                            "handler failed for update %s",
                            update.get("update_id"),
                        )
                backoff = POLL_BACKOFF_INITIAL  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("getUpdates failed: %s (backoff %ss)", exc, backoff)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=backoff,
                    )
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2 if backoff else 1, POLL_BACKOFF_MAX)

    # -------- update routing --------

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")

        if message is not None:
            from_id = (message.get("from") or {}).get("id")
            if from_id != self.allowed_user_id:
                log.info("dropping message from unauthorized user %s", from_id)
                return
            chat_id = (message.get("chat") or {}).get("id")
            text = (message.get("text") or "").strip()
            await self._handle_command(chat_id=chat_id, text=text)
            return

        if callback is not None:
            from_id = (callback.get("from") or {}).get("id")
            if from_id != self.allowed_user_id:
                log.info("dropping callback from unauthorized user %s", from_id)
                return
            await self._handle_callback(callback)
            return

    async def _handle_command(self, *, chat_id: int, text: str) -> None:
        if not text.startswith("/"):
            return

        head, *rest = text.split(maxsplit=1)
        cmd = head.lower()
        # Strip any "@botname" suffix TG adds in groups
        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]
        arg = rest[0] if rest else ""

        if cmd == "/menu":
            await self._cmd_menu(chat_id)
        elif cmd == "/status":
            await self._cmd_status(chat_id)
        elif cmd == "/stop":
            await self._cmd_stop(chat_id, arg)
        elif cmd == "/start":
            await self._cmd_start(chat_id, arg)
        else:
            await self._send_message(
                chat_id=chat_id,
                text=(
                    "Unknown command. Try:\n"
                    "/menu — control keyboard\n"
                    "/status — current state\n"
                    "/stop <sol|zec|all>\n"
                    "/start <sol|zec|all>"
                ),
            )

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        cb_id: str = callback.get("id", "")
        data: str = callback.get("data", "")
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        action, _, target = data.partition(":")

        # Status button: no state change → can't editMessageText (Telegram
        # rejects "not modified"). Show the current state as a popup alert
        # so the operator gets visible feedback.
        if action == "status":
            await self._answer_callback(
                callback_id=cb_id,
                text=self._render_status_text(),
                show_alert=True,
            )
            return

        toast = ""
        try:
            if action == "stop" and target:
                await self.gate.pause(target)
                self.store.cancel_pending_signals_for_symbol(target)
                toast = f"{target.split('-')[0]} paused"
            elif action == "start" and target:
                await self.gate.resume(target)
                toast = f"{target.split('-')[0]} running"
            else:
                toast = "Unknown action"
        except ValueError as exc:
            toast = f"Error: {exc}"

        await self._edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=self._render_status_text(),
            reply_markup=self._render_keyboard(),
        )
        await self._answer_callback(callback_id=cb_id, text=toast)

    # -------- command handlers --------

    def _render_status_text(self) -> str:
        lines = ["Fin — control"]
        for sym, paused in self.gate.status().items():
            tag = "⏸ paused" if paused else "▶ running"
            lines.append(f"{sym}: {tag}")
        return "\n".join(lines)

    def _render_keyboard(self) -> dict[str, Any]:
        status = self.gate.status()
        symbols = sorted(status.keys())

        def stop_btn(sym: str) -> dict[str, str]:
            base = sym.split("-")[0]
            label = f"⏸ {base} paused" if status[sym] else f"⏸ Stop {base}"
            return {"text": label, "callback_data": f"stop:{sym}"}

        def start_btn(sym: str) -> dict[str, str]:
            base = sym.split("-")[0]
            label = f"▶ Start {base}" if status[sym] else f"▶ {base} running"
            return {"text": label, "callback_data": f"start:{sym}"}

        stop_row = [stop_btn(s) for s in symbols]
        start_row = [start_btn(s) for s in symbols]
        return {
            "inline_keyboard": [
                stop_row,
                start_row,
                [{"text": "📊 Status", "callback_data": "status"}],
            ]
        }

    def _resolve_alias(self, alias: str) -> Optional[str]:
        """Map 'sol' → 'SOL-USDT'. Returns 'all' for all, None for unknown."""
        key = alias.strip().lower()
        if key == "all":
            return "all"
        return self._aliases.get(key)

    async def _cmd_menu(self, chat_id: int) -> None:
        await self._send_message(
            chat_id=chat_id,
            text=self._render_status_text(),
            reply_markup=self._render_keyboard(),
        )

    async def _cmd_status(self, chat_id: int) -> None:
        await self._send_message(
            chat_id=chat_id,
            text=self._render_status_text(),
        )

    async def _cmd_stop(self, chat_id: int, arg: str) -> None:
        target = self._resolve_alias(arg)
        if target is None:
            await self._send_message(
                chat_id=chat_id,
                text=f"Unknown symbol '{arg}'. Try: sol, zec, all",
            )
            return

        if target == "all":
            cancelled_total = 0
            for sym in list(self.gate.status().keys()):
                await self.gate.pause(sym)
                cancelled_total += self.store.cancel_pending_signals_for_symbol(sym)
            await self._send_message(
                chat_id=chat_id,
                text=f"All symbols paused. {cancelled_total} pending signal(s) cancelled.",
            )
            return

        await self.gate.pause(target)
        cancelled = self.store.cancel_pending_signals_for_symbol(target)
        base = target.split("-")[0]
        await self._send_message(
            chat_id=chat_id,
            text=f"{base} paused. {cancelled} pending signal(s) cancelled.",
        )

    async def _cmd_start(self, chat_id: int, arg: str) -> None:
        target = self._resolve_alias(arg)
        if target is None:
            await self._send_message(
                chat_id=chat_id,
                text=f"Unknown symbol '{arg}'. Try: sol, zec, all",
            )
            return

        if target == "all":
            await self.gate.resume_all()
            await self._send_message(
                chat_id=chat_id, text="All symbols running.",
            )
            return

        await self.gate.resume(target)
        base = target.split("-")[0]
        await self._send_message(chat_id=chat_id, text=f"{base} running.")

    # -------- HTTP helpers --------

    async def _get_updates(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        params = {
            "timeout": LONG_POLL_TIMEOUT,
            "offset": self._offset,
            "allowed_updates": ["message", "callback_query"],
        }
        r = await self._client.get(
            f"{API_BASE}/bot{self.bot_token}/getUpdates",
            params=params,
            timeout=LONG_POLL_TIMEOUT + 5,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("result", [])

    async def _send_message(
        self, *, chat_id: int, text: str,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if self._client is None:
            return {}
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/sendMessage",
            json=body,
            timeout=10.0,
        )
        return r.json()

    async def _edit_message(
        self, *, chat_id: int, message_id: int, text: str,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if self._client is None:
            return {}
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/editMessageText",
            json=body,
            timeout=10.0,
        )
        return r.json()

    async def _answer_callback(
        self, *, callback_id: str, text: str = "",
        show_alert: bool = False,
    ) -> dict[str, Any]:
        if self._client is None:
            return {}
        body = {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": show_alert,
        }
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/answerCallbackQuery",
            json=body,
            timeout=10.0,
        )
        return r.json()
