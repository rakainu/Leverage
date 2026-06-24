"""Telegram notifications via direct HTTP.

Bot token + chat ID come from env vars (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
If either is missing, logs the message but skips the network call —
useful in dev / local smoke runs without exposing credentials.

All messages are prefixed FROM: LIGHTER-BRIDGE per Rich's agent-tag convention.

Hooks exposed:
  - notify_startup(cfg, restored=None)  — bridge came up (optional restored positions list)
  - notify_open(pos)                    — position opened
  - notify_close(symbol, side, entry_price, exit_price, pnl, reason, duration_s, max_state)
  - notify_daily(summary)               — daily KPI summary
  - notify_error(msg)                   — crash / serious problem alert

Visual conventions:
  Entries:   LONG = 🟢🚀   SHORT = 🔴🔻
  Closes:    Win = 🟢   Loss = 🔴   Near-zero = 🟡
  Exit:      trail_sl = 🎯   sl_be = 🛡️   sl = 🛑   tp_ceiling = 💎
  Account:   Up = 📈   Down = 📉
"""
from __future__ import annotations

import html
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)


# Per-bridge identity. Both the trail bridge and the pro-v3 bridge share one bot +
# chat, so without a distinct tag their DMs are indistinguishable. Override per
# container via TELEGRAM_SENDER_TAG (e.g. "PRO-V3", "LIGHTER-TRAIL").
SENDER_TAG = os.environ.get("TELEGRAM_SENDER_TAG", "LIGHTER-BRIDGE")
_TG_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# Threshold below which a close is treated as "near-zero" (yellow dot)
NEAR_ZERO_USD = 5.0

_EXIT_REASON_EMOJI = {
    "trail_sl":   "🎯",
    "sl_be":      "🛡️",
    "sl":         "🛑",
    "tp_ceiling": "💎",
    "tp":         "💰",
    "time":       "⏱️",
}

_EXIT_REASON_LABEL = {
    "trail_sl":   "trail stop",
    "sl_be":      "break-even stop",
    "sl":         "initial stop",
    "tp_ceiling": "TP ceiling",
    "tp":         "take-profit",
    "time":       "time stop",
}


def _credentials() -> tuple[Optional[str], Optional[str]]:
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
                log.info("Telegram sent (%d chars)", len(full))
                return True
    except Exception as exc:
        log.warning("Telegram send exception: %s", exc)
        return False


# ----- Helpers -----

def _esc(s) -> str:
    return html.escape(str(s))


def fmt_pnl(p: float) -> str:
    return f"${p:+,.2f}"


def _side_chip(side: str) -> str:
    return "🟢🚀 LONG" if side == "long" else "🔴🔻 SHORT"


def _pnl_dot(pnl: float) -> str:
    if pnl > NEAR_ZERO_USD:
        return "🟢"
    if pnl < -NEAR_ZERO_USD:
        return "🔴"
    return "🟡"


def _account_arrow(net: float) -> str:
    if net > 0:
        return "📈"
    if net < 0:
        return "📉"
    return "➖"


def _exit_chip(reason: str) -> str:
    emoji = _EXIT_REASON_EMOJI.get(reason, "•")
    label = _EXIT_REASON_LABEL.get(reason, reason)
    return f"{emoji} <b>{_esc(label)}</b>"


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ----- High-level notification helpers -----

async def notify_startup(cfg, restored: Optional[list] = None) -> None:
    """Bridge-came-up alert. If `restored` is provided, list the rehydrated positions."""
    lines = [
        f"🟢 <b>{SENDER_TAG.title()} UP</b>",
        f"Host: <code>{_esc(cfg.host)}</code>",
        f"Paper collateral: ${cfg.initial_collateral_usdc:,.0f}",
        f"Symbols: {', '.join(cfg.symbols.keys())}",
    ]
    lines.append(
        f"Entry: slope≥{cfg.entry.min_abs_slope_pct:.2f}%  "
        f"body{cfg.entry.block_body_band}  noSun"
    )
    lines.append(
        f"Exits: SL=${cfg.exits.sl_loss_usdt:.0f}  "
        f"BE=${cfg.exits.breakeven_usdt:.0f}  "
        f"trail_dist=${cfg.exits.trail_distance_usdt:.0f}"
    )
    if restored:
        lines.append("")
        lines.append(f"<b>♻️ Restored {len(restored)} open position(s):</b>")
        for pos in restored:
            chip = _side_chip(pos.side)
            lines.append(
                f"  • {chip} <b>{_esc(pos.symbol)}</b> "
                f"entry=<code>${pos.entry_price:.4f}</code> "
                f"size={pos.base_amount:g}"
            )
    await send("\n".join(lines))


async def notify_open(pos) -> None:
    chip = _side_chip(pos.side)
    msg = (
        f"<b>⚡ ENTRY  {chip}  {_esc(pos.symbol)}</b>\n"
        f"price=<code>${pos.entry_price:.4f}</code>  "
        f"size={pos.base_amount:g}  "
        f"notional=${pos.notional:,.0f}\n"
        f"initial SL=<code>${pos.sl_price:.4f}</code>"
    )
    await send(msg)


async def notify_close(
    symbol: str,
    side: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    reason: str,
    duration_s: int,
    max_state: int,
    starting_collateral: float = 2000.0,
) -> None:
    dot = _pnl_dot(pnl)
    arrow = _account_arrow(pnl)
    pnl_pct_account = pnl / starting_collateral * 100 if starting_collateral else 0.0
    side_chip = _side_chip(side)
    exit_chip = _exit_chip(reason)
    msg = (
        f"<b>{dot} CLOSE  {side_chip}  {_esc(symbol)}</b>\n"
        f"PnL: {arrow} <b>{fmt_pnl(pnl)}</b> ({pnl_pct_account:+.2f}% of ${starting_collateral:,.0f})\n"
        f"entry=<code>${entry_price:.4f}</code> → exit=<code>${exit_price:.4f}</code>\n"
        f"reason={exit_chip}  •  state={max_state}  •  "
        f"duration={_fmt_duration(duration_s)}"
    )
    await send(msg)


async def notify_daily(stats: dict, starting_collateral: float = 2000.0) -> None:
    """Daily summary. `stats` is a dict from db.summary() + optional account fields."""
    n = stats.get("n_closed", 0)
    if n == 0:
        body_lines = ["📊 <b>Daily summary</b>", "No closed trades in the last 24h."]
    else:
        wins = stats.get("wins", 0)
        wr = stats.get("win_rate", 0)
        net = stats.get("net_pnl", 0)
        dot = _pnl_dot(net)
        arrow = _account_arrow(net)
        body_lines = [
            "📊 <b>Daily summary</b>",
            f"Trades: {n}  •  Wins: {wins} ({wr * 100:.1f}%)",
            f"Net PnL: {dot} {arrow} <b>{fmt_pnl(net)}</b>",
            (
                f"🎯 trail: {stats.get('trail_exits', 0)}  •  "
                f"🛑 sl: {stats.get('sl_hits', 0)}  •  "
                f"💎 ceiling: {stats.get('ceiling_hits', 0)}"
            ),
        ]
    pv = stats.get("portfolio_value")
    if pv is not None:
        delta = pv - starting_collateral
        body_lines.append(
            f"Equity: <b>${pv:,.2f}</b>  "
            f"({_account_arrow(delta)} {delta / starting_collateral * 100:+.2f}% vs ${starting_collateral:,.0f} start)"
        )
    await send("\n".join(body_lines))


async def notify_error(msg: str) -> None:
    await send(f"⚠️ <b>ERROR</b>\n<code>{_esc(msg)[:1500]}</code>")
