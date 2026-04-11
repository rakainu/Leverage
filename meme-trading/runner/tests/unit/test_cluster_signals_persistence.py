"""ConvergenceDetector persists ClusterSignals to the cluster_signals table."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _ev(sig, wallet, mint, t):
    return BuyEvent(
        signature=sig,
        wallet_address=wallet,
        token_mint=mint,
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=t,
    )


@pytest.mark.asyncio
async def test_persists_cluster_signal(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
        db=db,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # Drain the signal from the bus (the test shouldn't leak it)
    sig_bus.get_nowait()

    async with db.conn.execute(
        "SELECT token_mint, wallet_count, wallets_json, tier_counts_json, "
        "convergence_seconds, mid_price_sol FROM cluster_signals"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "TOKEN"
    assert row[1] == 3
    assert sorted(json.loads(row[2])) == ["A1", "A2", "B1"]
    assert json.loads(row[3]) == {"A": 2, "B": 1}
    assert row[4] == 600  # 10 minutes
    assert row[5] == pytest.approx(0.00025)

    await db.close()


@pytest.mark.asyncio
async def test_detector_without_db_still_works():
    """db=None preserves existing Plan 1 unit-test behavior."""
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
        db=None,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    assert sig_bus.qsize() == 1
