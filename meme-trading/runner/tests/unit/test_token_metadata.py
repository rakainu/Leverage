"""Helius DAS getAsset metadata fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.token_metadata import TokenMetadataFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "das_getAsset_fungible.json"


@pytest.mark.asyncio
async def test_fetch_parses_metadata_from_recorded_response():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        meta = await fetcher.fetch("TestMint1111111111111111111111111111111111")

    assert meta is not None
    assert meta["symbol"] == "WIFHAT"
    assert meta["name"] == "WIF Hat"
    assert meta["decimals"] == 6
    assert meta["supply"] == 1000000000000000
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_on_error_response():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(500, json={"error": "oops"}))
        meta = await fetcher.fetch("Whatever")

    assert meta is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_on_missing_result():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": None}))
        meta = await fetcher.fetch("Whatever")

    assert meta is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_handles_missing_optional_fields():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    # Token with no metadata content (happens for brand-new tokens)
    stripped = {
        "jsonrpc": "2.0",
        "result": {
            "interface": "FungibleToken",
            "id": "Bare1111111111111111111111111111111111",
            "content": {},
            "token_info": {"decimals": 9, "supply": 0},
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=stripped))
        meta = await fetcher.fetch("Bare1111111111111111111111111111111111")

    assert meta is not None
    assert meta["symbol"] is None
    assert meta["name"] is None
    assert meta["decimals"] == 9
    await client.aclose()
