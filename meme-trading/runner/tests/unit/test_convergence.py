"""Convergence detector with A+B tier gating."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal, ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _ev(sig: str, wallet: str, mint: str, t: datetime) -> BuyEvent:
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
async def test_emits_signal_when_three_ab_wallets_within_window(tmp_path):
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=8)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=14)))

    signal: ClusterSignal = sig_bus.get_nowait()
    assert signal.token_mint == "TOKEN"
    assert signal.wallet_count == 3
    assert set(signal.wallets) == {"A1", "A2", "B1"}
    assert signal.tier_counts == {"A": 2, "B": 1}
    assert 0 <= signal.convergence_seconds <= 30 * 60


@pytest.mark.asyncio
async def test_does_not_count_c_tier_wallets(tmp_path):
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "C1": Tier.C, "C2": Tier.C}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "C1", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "C2", "TOKEN", base + timedelta(minutes=10)))

    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_window_expires_old_events():
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    # 40 minutes later (beyond 30m window)
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=40)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=45)))

    assert sig_bus.empty()   # only 2 remain in window


@pytest.mark.asyncio
async def test_same_wallet_twice_counts_once():
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A})
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A1", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "A1", "TOKEN", base + timedelta(minutes=10)))
    await det._process(_ev("s4", "A2", "TOKEN", base + timedelta(minutes=15)))

    # Only 2 distinct A+B wallets — not enough for min_wallets=3
    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_does_not_signal_same_cluster_twice():
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # First signal fires
    sig_bus.get_nowait()

    # Same cluster again — additional buys from the same set should not double-fire
    await det._process(_ev("s4", "A1", "TOKEN", base + timedelta(minutes=11)))
    await det._process(_ev("s5", "A2", "TOKEN", base + timedelta(minutes=12)))

    assert sig_bus.empty()


@pytest.mark.asyncio
async def test_mid_price_is_mean_of_cluster_prices():
    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)

    def price_ev(sig, w, price):
        return BuyEvent(
            signature=sig,
            wallet_address=w,
            token_mint="TOKEN",
            sol_amount=0.25,
            token_amount=1000,
            price_sol=price,
            block_time=base,
        )

    await det._process(price_ev("s1", "A1", 0.0001))
    await det._process(price_ev("s2", "A2", 0.0002))
    await det._process(price_ev("s3", "B1", 0.0003))

    sig = sig_bus.get_nowait()
    assert abs(sig.mid_price_sol - 0.0002) < 1e-9


@pytest.mark.asyncio
async def test_picks_up_weights_changes_at_runtime(tmp_path):
    """Editing weights.yaml during runtime changes detection thresholds."""
    from runner.config.weights_loader import WeightsLoader

    yaml_file = tmp_path / "weights.yaml"
    yaml_file.write_text(
        """
cluster:
  min_wallets: 4
  window_minutes: 30
"""
    )
    loader = WeightsLoader(yaml_file)

    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        weights=loader,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # With min_wallets=4, 3 wallets should NOT fire a signal
    assert sig_bus.empty()

    # Now lower the threshold by editing the YAML
    import time
    time.sleep(0.01)
    yaml_file.write_text(
        """
cluster:
  min_wallets: 3
  window_minutes: 30
"""
    )
    yaml_file.touch()

    # Next event triggers reload on check; now 3 wallets should fire
    await det._process(_ev("s4", "A1", "TOKEN2", base))
    await det._process(_ev("s5", "A2", "TOKEN2", base + timedelta(minutes=5)))
    await det._process(_ev("s6", "B1", "TOKEN2", base + timedelta(minutes=10)))

    signal = sig_bus.get_nowait()
    assert signal.token_mint == "TOKEN2"
    assert signal.wallet_count == 3
