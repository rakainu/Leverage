"""FollowThroughProbe — async 5-minute probe with DB + price check."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.follow_through import FollowThroughProbe


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _enriched(mint="M", mid_price=0.0001) -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=mid_price,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


@pytest.mark.asyncio
async def test_probe_counts_new_ab_wallets_and_scores_high(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    # Seed buy_events: 2 new A+B wallets after cluster.last_buy_time
    base_last = datetime(2026, 4, 11, 10, 10, tzinfo=timezone.utc)
    for i, wallet in enumerate(["A3", "B2"]):
        await db.conn.execute(
            """
            INSERT INTO buy_events
            (signature, wallet_address, token_mint, sol_amount,
             token_amount, price_sol, block_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"sig{i}",
                wallet,
                "M",
                0.5,
                1000,
                0.0001,
                (base_last + timedelta(minutes=2 + i)).isoformat(),
            ),
        )
    await db.conn.commit()

    tier_cache = _StubTierCache({"A3": Tier.A, "B2": Tier.B})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.0001,  # flat price
        "price_usd": 0.0001,
        "liquidity_usd": 20000.0,
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,  # zero-delay for test
    )

    result = await probe.apply(_enriched())

    assert result.passed is True
    # +2 A+B wallets → score 80
    assert result.sub_scores["follow_through"] == pytest.approx(80.0)
    assert result.evidence["new_ab_wallets"] == 2

    await db.close()


@pytest.mark.asyncio
async def test_probe_no_new_wallets_price_up_scores_70(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.000115,  # +15%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(70.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_price_dump_scores_zero(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})
    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.00008,  # -20%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(0.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_no_new_wallets_price_flat_scores_40(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})
    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.000098,  # -2%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(40.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_c_tier_wallets_not_counted(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    base_last = datetime(2026, 4, 11, 10, 10, tzinfo=timezone.utc)
    for i, wallet in enumerate(["C1", "C2", "C3"]):
        await db.conn.execute(
            """
            INSERT INTO buy_events
            (signature, wallet_address, token_mint, sol_amount,
             token_amount, price_sol, block_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"sig{i}",
                wallet,
                "M",
                0.5,
                1000,
                0.0001,
                (base_last + timedelta(minutes=2 + i)).isoformat(),
            ),
        )
    await db.conn.commit()

    tier_cache = _StubTierCache({"C1": Tier.C, "C2": Tier.C, "C3": Tier.C})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {"price_sol": 0.0001}

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    # C-tier wallets don't count → 0 new A+B + flat price → 40
    assert result.sub_scores["follow_through"] == pytest.approx(40.0)
    assert result.evidence["new_ab_wallets"] == 0

    await db.close()
