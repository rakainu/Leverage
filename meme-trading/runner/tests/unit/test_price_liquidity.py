"""DexScreener + Jupiter price/liquidity/slippage fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.price_liquidity import PriceLiquidityFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.asyncio
async def test_fetch_picks_highest_liquidity_pair_and_assembles_result():
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    ds = json.loads((FIX / "dexscreener_pairs.json").read_text())
    q025 = json.loads((FIX / "jupiter_quote_buy_025.json").read_text())
    q050 = json.loads((FIX / "jupiter_quote_buy_050.json").read_text())

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/TestMint1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=ds["pairs"]))

        mock.get("https://quote-api.jup.ag/v6/quote").mock(
            side_effect=[
                httpx.Response(200, json=q025),
                httpx.Response(200, json=q050),
            ]
        )

        result = await fetcher.fetch(
            "TestMint1111111111111111111111111111111111",
            sizes_sol=[0.25, 0.5],
        )

    assert result is not None
    # Picks highest liquidity pair (42k > 8k)
    assert result["price_usd"] == pytest.approx(0.0001)
    assert result["price_sol"] == pytest.approx(0.00000026)
    assert result["liquidity_usd"] == pytest.approx(42000.0)
    assert result["volume_24h_usd"] == pytest.approx(150000.0)
    assert result["pair_age_seconds"] is not None
    # Slippage map
    assert result["slippage_at_size_pct"][0.25] == pytest.approx(1.2, abs=0.5)
    assert result["slippage_at_size_pct"][0.5] == pytest.approx(2.8, abs=0.5)

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_dexscreener_empty():
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/Absent1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=[]))

        result = await fetcher.fetch(
            "Absent1111111111111111111111111111111111",
            sizes_sol=[0.25],
        )

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_partial_when_jupiter_fails():
    """DexScreener succeeds, Jupiter fails — result still populated minus slippage."""
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    ds = json.loads((FIX / "dexscreener_pairs.json").read_text())

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/TestMint1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=ds["pairs"]))
        mock.get("https://quote-api.jup.ag/v6/quote").mock(
            return_value=httpx.Response(500, json={})
        )

        result = await fetcher.fetch(
            "TestMint1111111111111111111111111111111111",
            sizes_sol=[0.25],
        )

    assert result is not None
    assert result["liquidity_usd"] == pytest.approx(42000.0)
    assert result["slippage_at_size_pct"] == {}
    await client.aclose()
