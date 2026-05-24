import pytest

from lighter_dashboard.marks import MarkCache


class _FakeOrder:
    def __init__(self, price):
        self.price = str(price)


class _FakeBook:
    def __init__(self, ask, bid):
        self.asks = [_FakeOrder(ask)]
        self.bids = [_FakeOrder(bid)]


class _FakeOrderApi:
    def __init__(self):
        self.calls = 0
        self.book = _FakeBook(101.0, 99.0)

    async def order_book_orders(self, market_id, limit):
        self.calls += 1
        return self.book


@pytest.mark.asyncio
async def test_get_mid_computes_midpoint():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=2.0)
    mc._order_api = _FakeOrderApi()
    assert await mc.get_mid("SOL") == 100.0


@pytest.mark.asyncio
async def test_get_mid_uses_cache_within_ttl():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=100.0)
    fake = _FakeOrderApi()
    mc._order_api = fake
    await mc.get_mid("SOL")
    await mc.get_mid("SOL")
    assert fake.calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_get_mid_serves_stale_on_error():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=0.0)
    fake = _FakeOrderApi()
    mc._order_api = fake
    first = await mc.get_mid("SOL")

    class _Boom:
        async def order_book_orders(self, market_id, limit):
            raise RuntimeError("REST down")

    mc._order_api = _Boom()
    assert await mc.get_mid("SOL") == first  # last good value
