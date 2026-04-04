"""WebSocket connection manager for live dashboard updates."""

import json
import logging
from fastapi import WebSocket

logger = logging.getLogger("smc.dashboard.ws")


class WebSocketManager:
    """Tracks active WebSocket connections and broadcasts updates."""

    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"Dashboard client connected ({len(self.active)} active)")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"Dashboard client disconnected ({len(self.active)} active)")

    async def broadcast(self, data: dict):
        """Send data to all connected dashboard clients."""
        if not self.active:
            return
        message = json.dumps(data, default=str)
        disconnected = []
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)
