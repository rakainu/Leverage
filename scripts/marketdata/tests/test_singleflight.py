import asyncio
import pytest
from lighter_gateway.singleflight import SingleFlight

@pytest.mark.asyncio
async def test_concurrent_same_key_runs_once():
    sf = SingleFlight()
    calls = {"n": 0}
    async def factory():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "result"
    results = await asyncio.gather(*[sf.do("k", factory) for _ in range(5)])
    assert results == ["result"] * 5
    assert calls["n"] == 1          # coalesced to a single execution

@pytest.mark.asyncio
async def test_different_keys_run_independently():
    sf = SingleFlight()
    calls = {"n": 0}
    async def factory():
        calls["n"] += 1
        return calls["n"]
    await asyncio.gather(sf.do("a", factory), sf.do("b", factory))
    assert calls["n"] == 2
