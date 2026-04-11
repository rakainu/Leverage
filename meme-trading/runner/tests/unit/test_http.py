"""Token-bucket rate-limited httpx client."""
import asyncio
import time

import httpx
import pytest
import respx

from runner.utils.http import RateLimitedClient, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_initial_burst():
    bucket = TokenBucket(rate_per_sec=5, capacity=5)

    # Five immediate acquires should not block.
    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05   # basically instant


@pytest.mark.asyncio
async def test_token_bucket_throttles_excess():
    bucket = TokenBucket(rate_per_sec=10, capacity=2)

    start = time.monotonic()
    for _ in range(5):
        await bucket.acquire()
    elapsed = time.monotonic() - start

    # We burned the 2 initial tokens, then must wait for 3 more at 10/s = 0.3s min.
    assert elapsed >= 0.25
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_rate_limited_client_applies_per_host_limit():
    client = RateLimitedClient(
        default_rps=100,
        per_host_rps={"api.slow.test": 5},
        timeout=5.0,
    )

    with respx.mock(base_url="https://api.slow.test") as mock:
        mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))

        start = time.monotonic()
        for _ in range(5):
            r = await client.get("https://api.slow.test/x")
            assert r.status_code == 200
        elapsed = time.monotonic() - start

        # 5 req at 5 RPS, capacity 5 burst -> should finish fast
        assert elapsed < 1.0

    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limited_client_queues_excess_without_raising():
    client = RateLimitedClient(
        default_rps=100,
        per_host_rps={"api.slow.test": 5},
        timeout=5.0,
    )

    with respx.mock(base_url="https://api.slow.test") as mock:
        mock.get("/x").mock(return_value=httpx.Response(200, json={"ok": True}))

        start = time.monotonic()
        results = await asyncio.gather(
            *(client.get("https://api.slow.test/x") for _ in range(10))
        )
        elapsed = time.monotonic() - start

        assert all(r.status_code == 200 for r in results)
        # capacity 5 + refill 5/s -> 10 requests should take at least ~0.8s
        assert elapsed >= 0.7

    await client.aclose()


@pytest.mark.asyncio
async def test_rate_limited_client_retries_on_429():
    client = RateLimitedClient(default_rps=100, timeout=5.0, max_retries=3)

    with respx.mock(base_url="https://api.ratey.test") as mock:
        route = mock.get("/y").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ]
        )

        r = await client.get("https://api.ratey.test/y")
        assert r.status_code == 200
        assert route.call_count == 3

    await client.aclose()
