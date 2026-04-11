"""End-to-end: BuyEvent through event_bus → cluster detector → signal_bus."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal, ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


@pytest.mark.asyncio
async def test_end_to_end_event_to_signal():
    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()

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

    runner_task = asyncio.create_task(detector.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate(
        [("s1", "A1"), ("s2", "A2"), ("s3", "B1")]
    ):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="MEME",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    signal: ClusterSignal = await asyncio.wait_for(signal_bus.get(), timeout=2.0)
    assert signal.token_mint == "MEME"
    assert signal.wallet_count == 3

    runner_task.cancel()
    try:
        await runner_task
    except asyncio.CancelledError:
        pass
