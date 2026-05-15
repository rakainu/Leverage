"""Sparse Telegram alerting. Signal-only by design — no per-wallet noise.

Uses httpx (sync) to call the Bot HTTP API directly so we don't need an asyncio loop in
the executor's hot path. The slash-command bot in :mod:`hlsm.telegram.bot` is the only
async piece.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import httpx

from hlsm.config import get_settings
from hlsm.convergence import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.executor.exit_policy import ExitDecision

log = logging.getLogger(__name__)


def format_convergence(ev: ConvergenceEvent) -> str:
    side = ev.side.value.upper()
    wallets = "\n".join(f"  • <code>{a[:6]}…{a[-4:]}</code>" for a in ev.wallet_addresses)
    return (
        f"<b>CONVERGENCE</b> · <b>{ev.coin}</b> {side}\n"
        f"{ev.wallet_count} ranked wallets in {ev.window_seconds // 60}min\n"
        f"{wallets}"
    )


def format_position_open(signal: Signal, pp: PaperPosition) -> str:
    side = pp.side.upper()
    return (
        f"<b>OPENED</b> · <b>{pp.coin}</b> {side}\n"
        f"Entry: {pp.entry_px}\n"
        f"Size: ${pp.margin_usdt} × {pp.leverage}x = ${pp.notional_usdt}\n"
        f"SL: {pp.sl_px} · TP: {pp.tp_px}\n"
        f"Signal #{signal.id}"
    )


def format_position_close(pp: PaperPosition, decision: ExitDecision, pnl_usdt: Decimal) -> str:
    side = pp.side.upper()
    reason = decision.value.replace("_", " ")
    sign = "+" if pnl_usdt >= 0 else ""
    pct_sign = "+" if (pp.realized_pnl_pct or Decimal(0)) >= 0 else ""
    return (
        f"<b>CLOSED</b> · <b>{pp.coin}</b> {side} · <i>{reason}</i>\n"
        f"Entry {pp.entry_px} → Exit {pp.exit_px}\n"
        f"PnL: {sign}${pnl_usdt:.2f} ({pct_sign}{pp.realized_pnl_pct}%)\n"
        f"Signal #{pp.signal_id}"
    )


def format_breaker_trip(day_pnl: Decimal) -> str:
    return (
        f"<b>⚠ CIRCUIT BREAKER TRIPPED</b>\n"
        f"Day PnL: ${day_pnl:.2f}\n"
        f"New entries paused. Send <code>/hlsm resume</code> when ready."
    )


def format_heartbeat(*, tracked_wallets: int, scored_wallets: int,
                    open_positions: int, day_pnl: Decimal) -> str:
    sign = "+" if day_pnl >= 0 else ""
    return (
        f"<b>♥ HLSM heartbeat</b>\n"
        f"tracked={tracked_wallets} scored={scored_wallets} "
        f"open={open_positions} day_pnl={sign}${day_pnl:.2f}"
    )


@dataclass
class AlertSender:
    """Stateless HTTP wrapper around the Telegram bot send-message endpoint."""

    bot_token: str
    chat_id: str
    timeout_seconds: float = 5.0

    @classmethod
    def from_settings(cls) -> "AlertSender":
        s = get_settings()
        return cls(bot_token=s.telegram_bot_token, chat_id=s.telegram_chat_id)

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            log.debug("telegram disabled (no bot_token or chat_id); skipping alert")
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = httpx.post(url, json=payload, timeout=self.timeout_seconds)
            if r.status_code >= 400:
                log.warning("telegram send failed: status=%s body=%s", r.status_code, r.text[:200])
                return False
        except Exception:  # noqa: BLE001
            log.exception("telegram send raised")
            return False
        return True
