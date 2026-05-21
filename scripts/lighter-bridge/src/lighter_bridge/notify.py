"""Telegram notifications via direct HTTP.

Bot token + chat ID come from env vars (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
If either is missing, logs the message but skips the network call —
useful in dev / local smoke runs without exposing credentials.

All messages are prefixed FROM: LIGHTER-BRIDGE per Rich's agent-tag convention.

Hooks exposed:
  - notify_startup(cfg)        — bridge came up
  - notify_open(pos)           — position opened
  - notify_close(symbol, pnl, reason, duration_s)
  - notify_daily(summary)      — daily KPI summary
  - notify_error(msg)          — crash / serious problem alert
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)


SENDER_TAG = "LIGHTER-BRIDGE"

_TG_BASE = "https://api.telegram.org/bot{token}/sendMessage"


def _credentials() -> tuple[Optional[str], Optional[str]]:
    """Read bot token + chat id from env. Returns (None, None) if either missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return None, None
    return token, chat_id


async def send(text: str) -> bool:
    """Send a text message to the configured chat. Returns True on success."""
    token, chat_id = _credentials()
    full = f"FROM: {SENDER_TAG}\n{text}"
    if not token:
        log.info("[telegram skipped — no creds] %s", full.replace("\n", " | "))
        return False
    url = _TG_BASE.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": full,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        ) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("Telegram send failed: HTTP %d %s", resp.status, body[:200])
                    return False
                return True
    except Exception as exc:
        log.warning("Telegram send exception: %s", exc)
        return False


# ----- Helpers -----

def _esc(s) -> str:
    return html.escape(str(s))


def fmt_pnl(p: float) -> str:
    return f"${p:+,.2f}"


# ----- High-level notification helpers -----

async def notify_startup(cfg) -> None:
    msg = (
        f"<b>Bridge UP</b>\n"
        f"Host: <code>{_esc(cfg.host)}</code>\n"
        f"Paper collateral: ${cfg.initial_collateral_usdc:,.0f}\n"
        f"Symbols: {', '.join(cfg.symbols.keys())}\n"
        f"Entry: slope>={cfg.entry.min_abs_slope_pct:.2f}%  "
        f"body{cfg.entry.block_body_band}  noSun\n"
        f"Exits: SL=${cfg.exits.sl_loss_usdt:.0f} "
        f"BE=${cfg.exits.breakeven_usdt:.0f} "
        f"trail_dist=${cfg.exits.trail_distance_usdt:.0f}"
    )
    await send(msg)


async def notify_open(pos) -> None:
    msg = (
        f"<b>ENTRY  {_esc(pos.symbol)} {pos.side.upper()}</b>\n"
        f"price=<code>${pos.entry_price:.4f}</code>  "
        f"size={pos.base_amount:g}  "
        f"notional=${pos.notional:,.0f}\n"
        f"SL=${pos.sl_price:.4f} (state 0)"
    )
    await send(msg)


async def notify_close(symbol: str, side: str, entry_price: float, exit_price: float,
                       pnl: float, reason: str, duration_s: int, max_state: int) -> None:
    pnl_pct_account = pnl / 2000 * 100  # of $2k paper account
    arrow = "📈" if pnl > 0 else "📉"
    msg = (
        f"<b>{arrow} CLOSE {_esc(symbol)} {side.upper()}  {fmt_pnl(pnl)} ({pnl_pct_account:+.2f}%)</b>\n"
        f"entry=<code>${entry_price:.4f}</code> → exit=<code>${exit_price:.4f}</code>\n"
        f"reason=<b>{_esc(reason)}</b>  max_state={max_state}  "
        f"duration={duration_s // 60}m {duration_s % 60}s"
    )
    await send(msg)


async def notify_daily(stats: dict) -> None:
    """Daily summary. `stats` is a dict from db.summary() + account snapshot."""
    n = stats.get("n_closed", 0)
    if n == 0:
        body = (
            "<b>📊 Daily summary</b>\n"
            "No closed trades in the last 24h."
        )
    else:
        wins = stats.get("wins", 0)
        wr = stats.get("win_rate", 0)
        net = stats.get("net_pnl", 0)
        body = (
            f"<b>📊 Daily summary</b>\n"
            f"Trades: {n}  Wins: {wins} ({wr*100:.1f}%)\n"
            f"Net PnL: {fmt_pnl(net)}\n"
            f"SL hits: {stats.get('sl_hits', 0)}  "
            f"Trail exits: {stats.get('trail_exits', 0)}  "
            f"Ceiling: {stats.get('ceiling_hits', 0)}"
        )
    pv = stats.get("portfolio_value")
    if pv is not None:
        body += f"\nPortfolio: <b>${pv:,.2f}</b> ({(pv-2000)/2000*100:+.2f}% vs $2k start)"
    await send(body)


async def notify_error(msg: str) -> None:
    await send(f"<b>⚠️ ERROR</b>\n<code>{_esc(msg)[:1500]}</code>")
