"""Transaction parser extracts BuyEvent from Helius getTransaction response."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.ingest.rpc_pool import RpcPool
from runner.ingest.transaction_parser import TransactionParser
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "helius_getTransaction_buy.json"


@pytest.mark.asyncio
async def test_parses_buy_event_from_recorded_response():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))

        ev = await parser.parse_transaction(
            "TestSig11111111111111111111111111111111111111",
            "TestWallet11111111111111111111111111111111",
        )

    assert ev is not None
    assert ev.signature == "TestSig11111111111111111111111111111111111111"
    assert ev.wallet_address == "TestWallet11111111111111111111111111111111"
    assert ev.token_mint == "TestTokenMint111111111111111111111111111111"
    assert abs(ev.sol_amount - 0.5) < 1e-9
    assert abs(ev.token_amount - 1250.0) < 1e-9
    assert ev.price_sol == pytest.approx(0.5 / 1250.0)
    # blockTime 1744372800 is 2025-04-11 ≈ 12:00 UTC — year could be 2025
    assert ev.block_time.year in (2025, 2026)

    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_for_non_buy_transaction():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "blockTime": 1744372800,
            "meta": {
                "err": None,
                "fee": 5000,
                "preTokenBalances": [],
                "postTokenBalances": [],
            },
            "transaction": {"signatures": ["sig"]},
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    await client.aclose()


@pytest.mark.asyncio
async def test_returns_none_on_rpc_null_result():
    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": None}))

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    await client.aclose()
