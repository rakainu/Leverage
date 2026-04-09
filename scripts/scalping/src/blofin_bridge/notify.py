"""Telegram notifier. No-op when unconfigured."""
from __future__ import annotations
import logging

import httpx

log = logging.getLogger(__name__)


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
            "text": f"FROM: BLOFIN_BRIDGE\n{text}",
        }
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            httpx.post(url, json=body, timeout=5.0)
        except Exception as exc:
            log.warning("telegram send failed: %s", exc)
