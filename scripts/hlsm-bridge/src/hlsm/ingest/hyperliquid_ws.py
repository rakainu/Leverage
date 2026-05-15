"""Hyperliquid WebSocket monitor. Streams user-level position changes for a tracked set.

Emits :class:`hlsm.convergence.events.WalletOpenEvent` and :class:`WalletCloseEvent`
through the supplied callback. The convergence detector consumes these.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Iterable

import websockets

from hlsm.convergence.events import WalletCloseEvent, WalletOpenEvent
from hlsm.exchange.types import Side

log = logging.getLogger(__name__)

WS_URL = "wss://api.hyperliquid.xyz/ws"


OpenHandler = Callable[[WalletOpenEvent], Awaitable[None]]
CloseHandler = Callable[[WalletCloseEvent], Awaitable[None]]


class HyperliquidWebSocket:
    """Lightweight WS client. Subscribes to webData2 per user and emits position-change events."""

    def __init__(self, *, url: str = WS_URL,
                 on_open: OpenHandler | None = None,
                 on_close: CloseHandler | None = None,
                 score_provider: Callable[[str], float | None] | None = None) -> None:
        self.url = url
        self.on_open = on_open
        self.on_close = on_close
        self.score_provider = score_provider or (lambda _addr: None)
        self._last_positions: dict[tuple[str, str], Decimal] = {}  # (addr, coin) -> signed size
        self._stop = asyncio.Event()

    async def run(self, addresses: Iterable[str]) -> None:
        addrs = list(addresses)
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20) as ws:
                    for addr in addrs:
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "webData2", "user": addr},
                        }))
                    log.info("HL WS connected; subscribed to %d users", len(addrs))
                    async for raw in ws:
                        try:
                            await self._handle(raw)
                        except Exception:  # noqa: BLE001
                            log.exception("ws message handler raised")
            except Exception:  # noqa: BLE001
                log.exception("WS connection error; reconnecting in 5s")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self._stop.set()

    async def _handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except Exception:  # noqa: BLE001
            return
        channel = msg.get("channel")
        if channel != "webData2":
            return
        data = msg.get("data") or {}
        user = (data.get("user") or "").lower()
        cs = data.get("clearinghouseState") or {}
        positions = cs.get("assetPositions") or []
        now = datetime.now(timezone.utc)
        score = self.score_provider(user)
        if score is None:
            return  # Unscored wallet: ignore — convergence requires a score floor
        # Reconcile against last known sizes
        seen_keys: set[tuple[str, str]] = set()
        for p in positions:
            inner = p.get("position") or {}
            coin = str(inner.get("coin") or "").upper()
            if not coin:
                continue
            sz = Decimal(str(inner.get("szi") or 0))
            key = (user, coin)
            seen_keys.add(key)
            prev = self._last_positions.get(key, Decimal("0"))
            self._last_positions[key] = sz
            if sz != 0 and prev == 0:
                side = Side.LONG if sz > 0 else Side.SHORT
                if self.on_open is not None:
                    await self.on_open(WalletOpenEvent(
                        wallet_address=user, score=float(score), coin=coin, side=side, ts=now,
                    ))
            elif sz == 0 and prev != 0:
                side = Side.LONG if prev > 0 else Side.SHORT
                if self.on_close is not None:
                    await self.on_close(WalletCloseEvent(
                        wallet_address=user, coin=coin, side=side, ts=now,
                    ))
            elif (sz > 0) != (prev > 0) and prev != 0:
                # Flip
                old_side = Side.LONG if prev > 0 else Side.SHORT
                new_side = Side.LONG if sz > 0 else Side.SHORT
                if self.on_close is not None:
                    await self.on_close(WalletCloseEvent(
                        wallet_address=user, coin=coin, side=old_side, ts=now,
                    ))
                if self.on_open is not None:
                    await self.on_open(WalletOpenEvent(
                        wallet_address=user, score=float(score), coin=coin, side=new_side, ts=now,
                    ))

        # Any (user, coin) we knew about but didn't see this snapshot is now flat
        stale = [k for k in list(self._last_positions.keys())
                 if k[0] == user and k not in seen_keys and self._last_positions[k] != 0]
        for k in stale:
            prev = self._last_positions[k]
            self._last_positions[k] = Decimal("0")
            side = Side.LONG if prev > 0 else Side.SHORT
            if self.on_close is not None:
                await self.on_close(WalletCloseEvent(
                    wallet_address=k[0], coin=k[1], side=side, ts=now,
                ))


class LiveMonitor:
    """High-level wrapper: WS -> ConvergenceDetector -> Executor + Event-row writes."""

    def __init__(self, *, ws: HyperliquidWebSocket) -> None:
        self.ws = ws

    async def run(self, addresses: Iterable[str]) -> None:
        await self.ws.run(addresses)
