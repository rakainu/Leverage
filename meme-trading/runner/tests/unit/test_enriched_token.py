"""EnrichedToken dataclass carries everything a candidate knows about itself."""
from datetime import datetime, timedelta, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken


def _sig() -> ClusterSignal:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )


def test_can_construct_with_full_fields():
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        symbol="WIFHAT",
        name="WIF Hat",
        decimals=6,
        supply=1_000_000_000.0,
        token_created_at=datetime(2026, 4, 11, 9, 30, tzinfo=timezone.utc),
        price_sol=0.00026,
        price_usd=0.0001,
        liquidity_usd=42000.0,
        volume_24h_usd=150000.0,
        pair_age_seconds=1800,
        slippage_at_size_pct={0.25: 1.2, 0.5: 2.8},
        deployer_address="Deployer1",
        deployer_age_seconds=3600 * 24 * 7,
        deployer_token_count=3,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
        errors=[],
    )

    assert enriched.token_mint == "MINT"
    assert enriched.cluster_signal.wallet_count == 3
    assert enriched.symbol == "WIFHAT"
    assert enriched.slippage_at_size_pct[0.25] == 1.2
    assert enriched.errors == []


def test_optional_fields_default_to_none():
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
    )

    assert enriched.symbol is None
    assert enriched.name is None
    assert enriched.price_sol is None
    assert enriched.deployer_address is None
    assert enriched.slippage_at_size_pct == {}
    assert enriched.errors == []


def test_is_frozen():
    import dataclasses
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
    )
    try:
        enriched.symbol = "NEW"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("EnrichedToken must be frozen")
