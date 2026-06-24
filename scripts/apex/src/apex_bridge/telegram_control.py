"""Telegram control listener for the Apex bridge.

Inbound counterpart to notify.py (which is send-only). Lets Rich flip per-ticker
entry switches from his phone:

    /off SYM     block NEW entries on SYM (open positions still managed to exit)
    /on SYM      re-enable new entries on SYM
    /close SYM   force-close an open position on SYM now (market)
    /status      show per-symbol on/off + open positions
    /help        command list

Design:
  - Long-polls getUpdates with offset tracking on the EXISTING bridge bot token.
  - Authorized to ONE chat id (TELEGRAM_CHAT_ID) — every other sender is ignored.
  - Resilient: any network/parse error is caught, logged, backed off; the loop
    never crashes the bridge.
  - Pure helpers (parse_command, is_authorized, entries_allowed) carry no I/O so
    they are unit-tested directly; the runtime class wires them to callbacks the
    Bridge provides, keeping this module ignorant of trading internals (and of the
    `lighter` SDK), so it works unchanged against a live executor.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import aiohttp

log = logging.getLogger("control")

_ACTIONS_NEED_SYMBOL = {"off", "on", "close"}
_ACTIONS_NO_SYMBOL = {"status", "help", "kill"}
_ALL_ACTIONS = _ACTIONS_NEED_SYMBOL | _ACTIONS_NO_SYMBOL


@dataclass
class ParsedCommand:
    action: str                  # off | on | close | status | help
    symbol: Optional[str]        # uppercased, validated against known symbols
    error: Optional[str]         # user-facing error, or None


def entries_allowed(switches: dict, symbol: str) -> bool:
    """Pure gating predicate. Missing symbol = ON (default-allow)."""
    return bool(switches.get(symbol, True))


def is_authorized(chat_id, configured) -> bool:
    """True only if the sender chat matches the configured chat (str/int agnostic)."""
    if configured is None:
        return False
    return str(chat_id) == str(configured)


def parse_command(text: str, known_symbols) -> Optional[ParsedCommand]:
    """Parse a slash command. Returns None for non-commands (ignored).

    For a recognized action with a bad/missing symbol, returns a ParsedCommand
    whose .error is set (so the caller can reply with usage), symbol=None.
    """
    if not text:
        return None
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    head = parts[0][1:]                       # drop the leading "/"
    head = head.split("@", 1)[0].lower()      # tolerate "/status@botname"
    if head not in _ALL_ACTIONS:
        return None

    if head in _ACTIONS_NO_SYMBOL:
        return ParsedCommand(action=head, symbol=None, error=None)

    if len(parts) < 2:
        return ParsedCommand(action=head, symbol=None,
                             error=f"Usage: /{head} SYMBOL")
    symbol = parts[1].upper()
    if symbol == "ALL":
        return ParsedCommand(action=head, symbol="ALL", error=None)
    if symbol not in known_symbols:
        known = ", ".join(sorted(known_symbols))
        return ParsedCommand(action=head, symbol=None,
                             error=f"Unknown symbol '{symbol}'. Known: {known}")
    return ParsedCommand(action=head, symbol=symbol, error=None)


# Callback signatures the Bridge supplies. Each returns a reply string.
SetSwitch = Callable[[str, bool], Awaitable[str]]   # (symbol, enabled) -> reply
ForceClose = Callable[[str], Awaitable[str]]        # (symbol) -> reply
Status = Callable[[], Awaitable[str]]               # () -> reply


class TelegramControl:
    """getUpdates long-poll loop dispatching authorized commands to callbacks."""

    _API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, *, token: Optional[str], chat_id, known_symbols,
                 on_set_switch: SetSwitch, on_force_close: ForceClose,
                 on_status: Status, send=None):
        self.token = token
        self.chat_id = chat_id
        self.known = set(known_symbols)
        self.on_set_switch = on_set_switch
        self.on_force_close = on_force_close
        self.on_status = on_status
        self._send = send                      # async fn(text)->bool; defaults to notify.send
        self._offset = 0
        self._stopped = False

    def stop(self):
        self._stopped = True

    async def _reply(self, text: str):
        try:
            if self._send is not None:
                await self._send(text)
            else:
                from . import notify
                await notify.send(text)
        except Exception as exc:
            log.warning("control reply failed: %s", exc)

    async def run_loop(self):
        if not self.token:
            log.info("Telegram control disabled (no bot token).")
            return
        if not self.chat_id:
            log.warning("Telegram control: no chat id configured — refusing to "
                        "listen (would accept commands from anyone).")
            return
        log.info("Telegram control listening (authorized chat=%s, symbols=%s)",
                 self.chat_id, sorted(self.known))
        url = self._API.format(token=self.token, method="getUpdates")
        backoff = 1
        while not self._stopped:
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=40)
                ) as session:
                    params = {"timeout": 30, "offset": self._offset,
                              "allowed_updates": '["message"]'}
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"getUpdates HTTP {resp.status}")
                        data = await resp.json()
                backoff = 1
                for upd in data.get("result", []):
                    self._offset = upd["update_id"] + 1
                    await self._handle_update(upd)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("control poll error: %s (backoff %ds)", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _handle_update(self, upd: dict):
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        text = msg.get("text") or ""
        if not is_authorized(chat_id, self.chat_id):
            log.info("ignoring command from unauthorized chat %s", chat_id)
            return
        cmd = parse_command(text, self.known)
        if cmd is None:
            return
        if cmd.error:
            await self._reply(f"⚠️ {cmd.error}")
            return
        await self._dispatch(cmd)

    async def _dispatch(self, cmd: ParsedCommand):
        try:
            if cmd.action == "status":
                await self._reply(await self.on_status())
            elif cmd.action == "help":
                await self._reply(
                    "Apex control:\n"
                    "/kill — STOP ALL: close every open position + block all entries\n"
                    "/off SYM|ALL — block new entries (open trade still managed)\n"
                    "/on SYM|ALL — re-enable entries\n"
                    "/close SYM|ALL — force-close open position(s) now\n"
                    "/status — show on/off + open positions"
                )
            elif cmd.action == "kill":
                await self._reply(await self._kill_all())
            elif cmd.action in ("off", "on"):
                enabled = cmd.action == "on"
                if cmd.symbol == "ALL":
                    await self._reply(await self._switch_all(enabled))
                else:
                    await self._reply(await self.on_set_switch(cmd.symbol, enabled))
            elif cmd.action == "close":
                if cmd.symbol == "ALL":
                    await self._reply(await self._close_all())
                else:
                    await self._reply(await self.on_force_close(cmd.symbol))
        except Exception as exc:
            log.error("control dispatch error on %s: %s", cmd, exc, exc_info=True)
            await self._reply(f"⚠️ command failed: {exc}")


    async def _switch_all(self, enabled: bool) -> str:
        for sym in sorted(self.known):
            await self.on_set_switch(sym, enabled)
        state = "🟢 ON" if enabled else "⛔ OFF"
        return f"ALL entries {state} ({len(self.known)} symbols)"

    async def _close_all(self) -> str:
        closed = []
        for sym in sorted(self.known):
            r = await self.on_force_close(sym)
            if "no open position" not in r:
                closed.append(r)
        return "\n".join(closed) if closed else "No open positions to close."

    async def _kill_all(self) -> str:
        for sym in sorted(self.known):
            await self.on_set_switch(sym, False)
        closed = []
        for sym in sorted(self.known):
            r = await self.on_force_close(sym)
            if "no open position" not in r:
                closed.append(r)
        head = f"🛑 KILL — all entries OFF ({len(self.known)} symbols)"
        return head + (" · flattened:\n" + "\n".join(closed) if closed
                       else " · no open positions")
