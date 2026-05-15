"""Telegram surface: bot for slash commands + sparse signal-only alerts."""
from hlsm.telegram.alerts import (
    AlertSender,
    format_breaker_trip,
    format_convergence,
    format_heartbeat,
    format_position_close,
    format_position_open,
)
from hlsm.telegram.bot import TelegramBot

__all__ = [
    "AlertSender",
    "TelegramBot",
    "format_convergence",
    "format_position_open",
    "format_position_close",
    "format_breaker_trip",
    "format_heartbeat",
]
