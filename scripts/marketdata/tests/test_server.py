import pytest
from aiohttp import web
from lighter_gateway.config import GatewayConfig
from lighter_gateway.core import Gateway
from lighter_gateway.server import build_app

def _cfg():
    return GatewayConfig(upstream="https://up", host="0.0.0.0", port=8060,
                         rate_per_s=10.0, burst=10.0, max_stale_s=30.0,
                         cache_capacity=100, ttl={"/api/v1/orderBook": 2.5}, default_ttl=2.0)

@pytest.fixture
def gw():
    n = {"n": 0}
    async def fetch(method, path, query):
        n["n"] += 1
        return (200, b'{"mid": 1.23}', "application/json")
    g = Gateway(_cfg(), fetch)
    g._test_counter = n
    return g

async def test_health(aiohttp_client, gw):
    client = await aiohttp_client(build_app(gw))
    resp = await client.get("/__gw/health")
    assert resp.status == 200
    assert (await resp.json())["ok"] is True

async def test_proxy_and_cache(aiohttp_client, gw):
    client = await aiohttp_client(build_app(gw))
    r1 = await client.get("/api/v1/orderBook?market_id=2")
    assert r1.status == 200 and (await r1.json())["mid"] == 1.23
    r2 = await client.get("/api/v1/orderBook?market_id=2")
    assert r2.status == 200
    assert gw._test_counter["n"] == 1            # second served from cache
    stats = await (await client.get("/__gw/stats")).json()
    assert stats["hits"] >= 1 and stats["upstream_calls"] == 1
