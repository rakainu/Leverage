from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode

from .cache import CachedResponse, ResponseCache
from .config import GatewayConfig
from .ratelimit import TokenBucket
from .singleflight import SingleFlight

FetchFn = Callable[[str, str, str], Awaitable[tuple[int, bytes, str]]]


@dataclass
class GatewayResponse:
    status: int
    body: bytes
    content_type: str
    source: str   # hit | miss | stale | throttled | passthrough


def _norm_query(query: str) -> str:
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    return urlencode(pairs)


class Gateway:
    def __init__(self, cfg: GatewayConfig, fetch: FetchFn, *,
                 clock: Callable[[], float] = time.monotonic,
                 cache: ResponseCache | None = None,
                 bucket: TokenBucket | None = None):
        self.cfg = cfg
        self._fetch = fetch
        self._clock = clock
        self._cache = cache or ResponseCache(cfg.cache_capacity)
        self._bucket = bucket or TokenBucket(cfg.rate_per_s, cfg.burst, clock=clock)
        self._sf = SingleFlight()
        self._stats = {"upstream_calls": 0, "hits": 0, "misses": 0,
                       "stale_served": 0, "throttled": 0, "upstream_non2xx": 0}

    def stats(self) -> dict:
        return dict(self._stats)

    def _key(self, method: str, path: str, query: str) -> str:
        return f"{method} {path}?{_norm_query(query)}"

    async def handle(self, method: str, path: str, query: str) -> GatewayResponse:
        if method.upper() != "GET":
            status, body, ct = await self._upstream(method, path, query)
            return GatewayResponse(status, body, ct, "passthrough")

        key = self._key(method, path, query)
        ttl = self.cfg.ttl_for(path)
        entry = self._cache.get(key)
        if entry is not None and (self._clock() - entry.fetched_monotonic) <= ttl:
            self._stats["hits"] += 1
            return GatewayResponse(entry.status, entry.body, entry.content_type, "hit")

        async def factory() -> GatewayResponse:
            # Re-check: a concurrent caller may have just refreshed.
            e2 = self._cache.get(key)
            if e2 is not None and (self._clock() - e2.fetched_monotonic) <= ttl:
                self._stats["hits"] += 1
                return GatewayResponse(e2.status, e2.body, e2.content_type, "hit")
            if not self._bucket.try_acquire():
                # Throttled: serve any acceptably-fresh stale copy, else 503.
                if e2 is not None and (self._clock() - e2.fetched_monotonic) <= self.cfg.max_stale_s:
                    self._stats["stale_served"] += 1
                    return GatewayResponse(e2.status, e2.body, e2.content_type, "stale")
                self._stats["throttled"] += 1
                return GatewayResponse(503, b'{"error":"gateway throttled"}',
                                       "application/json", "throttled")
            status, body, ct = await self._upstream(method, path, query)
            if 200 <= status < 300:
                self._cache.put(key, CachedResponse(status, body, ct, self._clock()))
            else:
                self._stats["upstream_non2xx"] += 1
            self._stats["misses"] += 1
            return GatewayResponse(status, body, ct, "miss")

        return await self._sf.do(key, factory)

    async def _upstream(self, method: str, path: str, query: str):
        self._stats["upstream_calls"] += 1
        return await self._fetch(method, path, query)
