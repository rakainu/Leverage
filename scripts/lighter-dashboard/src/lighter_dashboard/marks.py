"""Live order-book mid fetcher with a short TTL cache.

Independent of the bridge: makes its own Lighter public REST calls via
lighter.OrderApi.order_book_orders and computes (best_ask + best_bid)/2 —
the same mid the bridge's state machine uses, but sourced separately.
"""
from __future__ import annotations

import time
from typing import Optional

import lighter


class MarkCache:
    def __init__(self, host: str, symbols: dict[str, int], ttl: float = 2.0):
        self.host = host
        self.symbols = symbols            # name -> market_id
        self.ttl = ttl
        self._cache: dict[str, tuple[float, float]] = {}  # name -> (price, monotonic)
        self._api: Optional[lighter.ApiClient] = None
        self._order_api = None            # set lazily or injected in tests

    async def _ensure_api(self):
        if self._order_api is None:
            self._api = lighter.ApiClient(
                configuration=lighter.Configuration(host=self.host)
            )
            self._order_api = lighter.OrderApi(self._api)

    async def get_mid(self, name: str) -> Optional[float]:
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached and now - cached[1] < self.ttl:
            return cached[0]
        await self._ensure_api()
        mid = await self._fetch_mid(name)
        if mid is not None:
            self._cache[name] = (mid, now)
            return mid
        return cached[0] if cached else None   # serve stale on failure

    async def _fetch_mid(self, name: str) -> Optional[float]:
        market_id = self.symbols[name]
        try:
            ob = await self._order_api.order_book_orders(market_id=market_id, limit=1)
        except Exception:
            return None
        if not ob.asks or not ob.bids:
            return None
        ask = float(ob.asks[0].price)
        bid = float(ob.bids[0].price)
        if ask <= 0 or bid <= 0:
            return None
        return (ask + bid) / 2.0

    async def close(self):
        if self._api is not None:
            try:
                await self._api.close()
            except Exception:
                pass
