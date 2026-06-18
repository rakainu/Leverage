"""Telegram notifier with clean formatting."""
from __future__ import annotations
import logging
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


def format_entry(result: dict[str, Any]) -> str:
    """Minimal 'Fin' entry alert, signed from V3 — entry + SL only."""
    side = result.get("side", "?")
    symbol = str(result.get("symbol", "?")).replace("-USDT", "")
    entry = result.get("entry_price", 0)
    sl = result.get("sl_trigger", 0)
    icon = "🟢" if side == "long" else "🔴"
    direction = "LONG" if side == "long" else "SHORT"
    return (
        f"{icon} V3 · {direction} {symbol}\n"
        f"Entry ${entry:,.2f}  ·  SL ${sl:,.2f}"
    )


def format_exit(symbol: str, side: str, pnl_usdt: float, exit_reason: str = "") -> str:
    """Minimal 'Fin' exit alert, signed from V3 — just the ±P&L."""
    sym = str(symbol).replace("-USDT", "")
    win = pnl_usdt >= 0
    icon = "🟢" if win else "🔴"
    sign = "+" if win else "−"
    return f"{icon} V3 · {sym} exit  {sign}${abs(pnl_usdt):,.2f}"


def format_trail_activated(symbol: str, pnl: float, sl_price: float) -> str:
    return (
        f"📈 TRAIL ACTIVE {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"💰 P&L: +${pnl:,.2f}\n"
        f"🛑 SL moved to: ${sl_price:,.2f}"
    )


def format_trail_update(symbol: str, new_high: float, sl_price: float) -> str:
    return (
        f"📈 TRAIL ↑ {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔝 New high: ${new_high:,.2f}\n"
        f"🛑 SL → ${sl_price:,.2f}"
    )


def format_pending(action: str, symbol: str, signal_price: float) -> str:
    icon = "🟢" if action == "buy" else "🔴"
    direction = "LONG" if action == "buy" else "SHORT"
    return (
        f"⏳ PENDING {icon} {direction} {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 Signal: ${signal_price:,.2f}\n"
        f"⏱️ Waiting for EMA(9) retest"
    )


def format_pending_filled(action: str, symbol: str, fill_price: float, signal_price: float) -> str:
    icon = "🟢" if action == "buy" else "🔴"
    direction = "LONG" if action == "buy" else "SHORT"
    diff = fill_price - signal_price
    return (
        f"✅ EMA RETEST → {icon} {direction} {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📍 Signal: ${signal_price:,.2f}\n"
        f"📍 Entry: ${fill_price:,.2f} ({diff:+,.2f})"
    )


def format_pending_expired(action: str, symbol: str) -> str:
    direction = "LONG" if action == "buy" else "SHORT"
    return (
        f"⌛ EXPIRED {direction} {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"No EMA(9) retest — signal cancelled"
    )


def format_error(action: str, symbol: str, error: str) -> str:
    return (
        f"⚠️ ERROR {action.upper()} {symbol}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{error}"
    )


class Notifier:
    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        body = {
            "chat_id": self.chat_id,
            "text": text,
        }
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            httpx.post(url, json=body, timeout=5.0)
        except Exception as exc:
            log.warning("telegram send failed: %s", exc)
