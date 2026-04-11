"""Helius-based deployer history fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.deployer import DeployerFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.asyncio
async def test_fetch_identifies_deployer_from_earliest_tx():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    sigs_body = json.loads((FIX / "helius_signatures_mint_init.json").read_text())
    tx_body = json.loads((FIX / "helius_getTransaction_mint_init.json").read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        def _router(request):
            body = json.loads(request.content)
            if body["method"] == "getSignaturesForAddress":
                return httpx.Response(200, json=sigs_body)
            if body["method"] == "getTransaction":
                return httpx.Response(200, json=tx_body)
            return httpx.Response(404, json={})

        mock.post("/rpc").mock(side_effect=_router)

        info = await fetcher.fetch("TestMint1111111111111111111111111111111111")

    assert info is not None
    assert info["deployer_address"] == "DeployerWallet111111111111111111111111111"
    assert info["deployer_first_tx_time"] is not None
    assert info["deployer_age_seconds"] is not None
    assert info["deployer_age_seconds"] > 0

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_signatures():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": []}))
        info = await fetcher.fetch("Nothing1111111111111111111111111111111")

    assert info is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_tx_has_no_signer():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    sigs_body = {
        "jsonrpc": "2.0",
        "result": [
            {"signature": "S1", "blockTime": 1744000000}
        ],
    }
    tx_body = {
        "jsonrpc": "2.0",
        "result": {
            "blockTime": 1744000000,
            "meta": {"err": None},
            "transaction": {
                "message": {"accountKeys": []},
                "signatures": ["S1"],
            },
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        def _router(request):
            body = json.loads(request.content)
            if body["method"] == "getSignaturesForAddress":
                return httpx.Response(200, json=sigs_body)
            return httpx.Response(200, json=tx_body)
        mock.post("/rpc").mock(side_effect=_router)

        info = await fetcher.fetch("NoSigner1111111111111111111111111111111")

    assert info is None
    await client.aclose()
