import pytest
from lighter_gateway.config import GatewayConfig
from lighter_gateway.core import Gateway

def _cfg(**kw):
    base = dict(upstream="https://up", host="0.0.0.0", port=8060, rate_per_s=1.0,
                burst=2.0, max_stale_s=30.0, cache_capacity=100,
                ttl={"/api/v1/orderBook": 2.5}, default_ttl=2.0)
    base.update(kw)
    return GatewayConfig(**base)

def make_fetch(counter, status=200, body=b'{"ok":1}', ct="application/json"):
    async def fetch(method, path, query):
        counter["n"] += 1
        return (status, body, ct)
    return fetch

@pytest.mark.asyncio
async def test_cache_hit_skips_upstream():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    r1 = await gw.handle("GET", "/api/v1/orderBook", "market_id=2")
    assert r1.source == "miss" and n["n"] == 1
    t[0] = 1.0                                   # within 2.5s TTL
    r2 = await gw.handle("GET", "/api/v1/orderBook", "market_id=2")
    assert r2.source == "hit" and n["n"] == 1    # no extra upstream call

@pytest.mark.asyncio
async def test_ttl_expiry_refetches():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")
    t[0] = 3.0                                    # past 2.5s TTL
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert r.source == "miss" and n["n"] == 2

@pytest.mark.asyncio
async def test_throttle_serves_stale():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(rate_per_s=0.0, burst=1.0), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")   # consumes the 1 burst token
    t[0] = 5.0                                            # past TTL, no refill (rate 0)
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert r.source == "stale" and n["n"] == 1           # served old body, no upstream

@pytest.mark.asyncio
async def test_query_order_independent_key():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "a=1&b=2")
    r = await gw.handle("GET", "/api/v1/orderBook", "b=2&a=1")
    assert r.source == "hit" and n["n"] == 1

@pytest.mark.asyncio
async def test_non_2xx_not_cached():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n, status=429, body=b"slow down"), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert n["n"] == 2 and r.status == 429               # never cached, refetched
