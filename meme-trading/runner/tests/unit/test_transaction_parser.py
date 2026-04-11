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
async def test_returns_none_on_rpc_null_result(monkeypatch):
    # Speed up retry sleep so the test doesn't take ~3 seconds.
    from runner.ingest import transaction_parser as tp
    monkeypatch.setattr(tp, "NULL_RETRY_SLEEP_SEC", 0.0)

    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": None}))

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    await client.aclose()


@pytest.mark.asyncio
async def test_parses_native_sol_buy_without_wsol():
    """A buy that uses native SOL (no wSOL ATA) — parser should still extract it."""
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
                "preBalances": [1500000000, 0],   # wallet has 1.5 SOL
                "postBalances": [995000, 0],       # now ~0.001 SOL (paid 0.5 SOL + fee)
                "preTokenBalances": [],
                "postTokenBalances": [
                    {
                        "accountIndex": 1,
                        "mint": "TestTokenMint111111111111111111111111111111",
                        "owner": "TestWallet11111111111111111111111111111111",
                        "uiTokenAmount": {"uiAmount": 1250.0, "decimals": 6}
                    }
                ]
            },
            "transaction": {
                "message": {
                    "accountKeys": [
                        {"pubkey": "TestWallet11111111111111111111111111111111", "signer": True, "writable": True},
                        {"pubkey": "TokenAta4444444444444444444444444444444444", "signer": False, "writable": True}
                    ]
                },
                "signatures": ["TestSig11111111111111111111111111111111111111"]
            }
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        ev = await parser.parse_transaction(
            "TestSig11111111111111111111111111111111111111",
            "TestWallet11111111111111111111111111111111",
        )

    assert ev is not None
    # Paid 1.5 SOL - 0.000995 SOL - 0.000005 fee = 1.499 SOL out
    assert abs(ev.sol_amount - 1.499) < 0.01
    assert ev.token_mint == "TestTokenMint111111111111111111111111111111"
    assert abs(ev.token_amount - 1250.0) < 1e-9

    await client.aclose()


@pytest.mark.asyncio
async def test_ambiguous_multi_token_deltas_return_none():
    """Two positive non-quote deltas — parser should log-and-skip, returning None."""
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
                "preBalances": [1000000000, 0],
                "postBalances": [500000000, 0],
                "preTokenBalances": [],
                "postTokenBalances": [
                    {
                        "accountIndex": 1,
                        "mint": "FirstTokenMint111111111111111111111111111",
                        "owner": "TestWallet11111111111111111111111111111111",
                        "uiTokenAmount": {"uiAmount": 500.0, "decimals": 6}
                    },
                    {
                        "accountIndex": 1,
                        "mint": "SecondTokenMint22222222222222222222222222",
                        "owner": "TestWallet11111111111111111111111111111111",
                        "uiTokenAmount": {"uiAmount": 1000.0, "decimals": 6}
                    }
                ]
            },
            "transaction": {
                "message": {
                    "accountKeys": [
                        {"pubkey": "TestWallet11111111111111111111111111111111", "signer": True, "writable": True}
                    ]
                },
                "signatures": ["sig"]
            }
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        ev = await parser.parse_transaction("sig", "TestWallet11111111111111111111111111111111")

    assert ev is None   # ambiguous — parser should refuse
    await client.aclose()


@pytest.mark.asyncio
async def test_null_result_retries_then_returns_none(monkeypatch):
    """Null result should retry, then return None after exhausting attempts."""
    # Speed up retry sleep so test doesn't take 3 seconds
    from runner.ingest import transaction_parser as tp
    monkeypatch.setattr(tp, "NULL_RETRY_SLEEP_SEC", 0.01)

    client = RateLimitedClient(default_rps=100)
    pool = RpcPool(["https://rpc.helius.test/rpc"])
    parser = TransactionParser(pool, client)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        route = mock.post("/rpc").mock(
            return_value=httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})
        )

        ev = await parser.parse_transaction("sig", "wallet")

    assert ev is None
    assert route.call_count == 3   # 3 attempts were made

    await client.aclose()
