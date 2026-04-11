"""End-to-end: BuyEvent → cluster → enrichment → filters → FilteredCandidate."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.enricher import Enricher
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.filters.pipeline import FilterPipeline
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


class _PassFilter(BaseFilter):
    def __init__(self, name: str, sub_scores: dict[str, float]):
        self.name = name  # type: ignore[misc]
        self._sub_scores = sub_scores

    async def apply(self, enriched):
        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores=self._sub_scores,
            evidence={},
        )


@pytest.mark.asyncio
async def test_full_pipeline_produces_filtered_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

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
        "symbol": "E2E",
        "decimals": 6,
        "supply": 1e9,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.00025,
        "liquidity_usd": 30000.0,
        "slippage_at_size_pct": {0.25: 1.0},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "Dep1"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=[
            _PassFilter("rug", {"rug_risk": 90}),
            _PassFilter("holder", {"holder_quality": 70}),
        ],
        probe_filter=_PassFilter("follow_through", {"follow_through": 100}),
        db=db,
    )

    det_task = asyncio.create_task(detector.run())
    enr_task = asyncio.create_task(enricher.run())
    pipe_task = asyncio.create_task(pipeline.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate([("s1", "A1"), ("s2", "A2"), ("s3", "B1")]):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="E2E_MINT",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    fc: FilteredCandidate = await asyncio.wait_for(filtered_bus.get(), timeout=3.0)
    assert fc.enriched.token_mint == "E2E_MINT"
    assert fc.enriched.symbol == "E2E"
    assert fc.gate_passed is True
    assert len(fc.filter_results) == 3
    assert fc.filter_results[0].filter_name == "rug"
    assert fc.filter_results[2].filter_name == "follow_through"

    for t in (det_task, enr_task, pipe_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    await db.close()
