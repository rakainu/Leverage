"""End-to-end: BuyEvent → cluster → enrichment → EnrichedToken."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.enrichment.enricher import Enricher
from runner.enrichment.schemas import EnrichedToken
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


@pytest.mark.asyncio
async def test_full_pipeline_produces_enriched_token():
    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    metadata = AsyncMock()
    metadata.fetch.return_value = {
        "symbol": "PIPE",
        "name": "Pipeline Token",
        "decimals": 6,
        "supply": 1e9,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.0003,
        "price_usd": 0.0002,
        "liquidity_usd": 25000.0,
        "volume_24h_usd": 80000.0,
        "pair_age_seconds": 1200,
        "slippage_at_size_pct": {0.25: 1.5},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {
        "deployer_address": "DepX",
        "deployer_age_seconds": 86400,
        "deployer_token_count": None,
    }

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    det_task = asyncio.create_task(detector.run())
    enr_task = asyncio.create_task(enricher.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate(
        [("s1", "A1"), ("s2", "A2"), ("s3", "B1")]
    ):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="PIPE_MINT",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    enriched: EnrichedToken = await asyncio.wait_for(enriched_bus.get(), timeout=3.0)
    assert enriched.token_mint == "PIPE_MINT"
    assert enriched.symbol == "PIPE"
    assert enriched.liquidity_usd == pytest.approx(25000.0)
    assert enriched.deployer_address == "DepX"
    assert enriched.cluster_signal.wallet_count == 3

    for t in (det_task, enr_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
