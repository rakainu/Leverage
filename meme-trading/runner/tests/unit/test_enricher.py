"""Enricher orchestrator: consumes ClusterSignal, produces EnrichedToken."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.enricher import Enricher
from runner.enrichment.schemas import EnrichedToken


def _sig(mint="MINT") -> ClusterSignal:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )


@pytest.mark.asyncio
async def test_enricher_assembles_enriched_token_from_all_fetchers():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = {
        "symbol": "WIFHAT",
        "name": "WIF Hat",
        "decimals": 6,
        "supply": 1_000_000_000.0,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.00026,
        "price_usd": 0.0001,
        "liquidity_usd": 42000.0,
        "volume_24h_usd": 150000.0,
        "pair_age_seconds": 1800,
        "slippage_at_size_pct": {0.25: 1.2},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {
        "deployer_address": "Dep1",
        "deployer_first_tx_time": datetime(2026, 4, 4, tzinfo=timezone.utc),
        "deployer_age_seconds": 7 * 24 * 3600,
        "deployer_token_count": None,
    }

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    sig = _sig()
    enriched = await enricher._enrich_one(sig)

    assert isinstance(enriched, EnrichedToken)
    assert enriched.token_mint == "MINT"
    assert enriched.symbol == "WIFHAT"
    assert enriched.price_sol == pytest.approx(0.00026)
    assert enriched.liquidity_usd == pytest.approx(42000.0)
    assert enriched.deployer_address == "Dep1"
    assert enriched.errors == []


@pytest.mark.asyncio
async def test_enricher_collects_errors_when_fetchers_return_none():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = None
    price = AsyncMock()
    price.fetch.return_value = None
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "Dep1"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    enriched = await enricher._enrich_one(_sig())
    assert enriched.symbol is None
    assert enriched.price_sol is None
    assert enriched.deployer_address == "Dep1"
    assert "metadata_unavailable" in enriched.errors
    assert "price_liquidity_unavailable" in enriched.errors
    assert "deployer_unavailable" not in enriched.errors


@pytest.mark.asyncio
async def test_enricher_run_consumes_signal_bus_and_produces_enriched_bus():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = {"symbol": "X", "decimals": 6, "supply": 1e9}
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.0001,
        "liquidity_usd": 10000.0,
        "slippage_at_size_pct": {},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "D"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    task = asyncio.create_task(enricher.run())
    try:
        await signal_bus.put(_sig())
        enriched = await asyncio.wait_for(enriched_bus.get(), timeout=2.0)
        assert enriched.token_mint == "MINT"
        assert enriched.symbol == "X"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
