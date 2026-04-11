"""Token-bucket rate-limited HTTP client built on httpx.AsyncClient."""
import asyncio
import time
from urllib.parse import urlparse

import httpx

from runner.utils.logging import get_logger

logger = get_logger("runner.utils.http")


class TokenBucket:
    """Simple async token bucket.

    `rate_per_sec` tokens are added to the bucket each second up to `capacity`.
    Each `acquire()` removes one token, blocking until one is available.
    """

    def __init__(self, rate_per_sec: float, capacity: int | None = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity if capacity is not None else rate_per_sec)
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_refill = now

                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                deficit = cost - self._tokens
                wait = deficit / self.rate
            # Sleep OUTSIDE the lock so other tasks can refill-check too.
            await asyncio.sleep(wait)


class RateLimitedClient:
    """httpx.AsyncClient wrapper with per-host token buckets + 429 retry."""

    def __init__(
        self,
        default_rps: float = 10.0,
        per_host_rps: dict[str, float] | None = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ):
        self._client = httpx.AsyncClient(timeout=timeout)
        self._default_rps = default_rps
        self._per_host_rps = per_host_rps or {}
        self._buckets: dict[str, TokenBucket] = {}
        self._buckets_lock = asyncio.Lock()
        self._max_retries = max_retries

    def _host_of(self, url: str) -> str:
        return urlparse(url).netloc.lower()

    async def _bucket_for(self, host: str) -> TokenBucket:
        async with self._buckets_lock:
            if host not in self._buckets:
                rps = self._per_host_rps.get(host, self._default_rps)
                # Capacity matches rate for a 1-second burst allowance.
                self._buckets[host] = TokenBucket(rps, capacity=max(1, int(rps)))
            return self._buckets[host]

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = self._host_of(url)
        bucket = await self._bucket_for(host)

        attempt = 0
        while True:
            await bucket.acquire()
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.HTTPError as e:
                if attempt >= self._max_retries:
                    raise
                backoff = min(2.0 ** attempt, 10.0)
                logger.warning(
                    "http_error_retry",
                    url=url,
                    error=str(e),
                    attempt=attempt,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                attempt += 1
                continue

            if resp.status_code == 429 and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else 2.0 ** attempt
                except ValueError:
                    wait = 2.0 ** attempt
                wait = max(0.0, min(wait, 10.0))
                logger.warning(
                    "http_429_retry",
                    url=url,
                    attempt=attempt,
                    wait=wait,
                )
                await asyncio.sleep(wait)
                attempt += 1
                continue

            if 500 <= resp.status_code < 600 and attempt < self._max_retries:
                backoff = min(2.0 ** attempt, 10.0)
                logger.warning(
                    "http_5xx_retry",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt,
                    backoff=backoff,
                )
                await asyncio.sleep(backoff)
                attempt += 1
                continue

            return resp

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()
