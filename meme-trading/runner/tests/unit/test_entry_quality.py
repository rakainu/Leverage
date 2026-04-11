"""EntryQualityFilter — pure computation of entry quality from EnrichedToken."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.entry_quality import EntryQualityFilter


def _enriched(
    mid_price=0.0001,
    current_price=0.0001,
    pair_age_seconds=600,
    slippage_25=1.0,
) -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=mid_price,
    )
    return EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
        price_sol=current_price,
        pair_age_seconds=pair_age_seconds,
        slippage_at_size_pct={0.25: slippage_25},
    )


@pytest.mark.asyncio
async def test_fresh_token_low_extension_scores_high():
    filt = EntryQualityFilter()

    # 0% extension, 10 min old, 1% slippage → near-perfect entry
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    # Base 100 (0% extension) + 15 (<30min) = 115 → cap 100
    assert result.sub_scores["entry_quality"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_extended_token_scores_low():
    filt = EntryQualityFilter()

    # 40% extension (30-60% band = 15 points), 2h old (0 mod), low slippage
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.00014,
        pair_age_seconds=2 * 3600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    assert result.sub_scores["entry_quality"] == pytest.approx(15.0, abs=1)


@pytest.mark.asyncio
async def test_stale_token_receives_penalty():
    filt = EntryQualityFilter()

    # 0% extension, 12h old → base 100 + (-10) = 90
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=12 * 3600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    assert result.sub_scores["entry_quality"] == pytest.approx(90.0, abs=1)


@pytest.mark.asyncio
async def test_high_slippage_caps_score_at_40():
    filt = EntryQualityFilter()

    # 0% extension, fresh token, 7% slippage (>5%) → capped at 40
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=600,
        slippage_25=7.0,
    )
    result = await filt.apply(enriched)

    assert result.sub_scores["entry_quality"] <= 40.0


@pytest.mark.asyncio
async def test_missing_price_data_scores_zero():
    filt = EntryQualityFilter()

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1"],
        wallet_count=1,
        tier_counts={"A": 1},
        first_buy_time=base,
        last_buy_time=base,
        convergence_seconds=0,
        mid_price_sol=0.0001,
    )
    enriched = EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base,
        price_sol=None,  # price unavailable
    )
    result = await filt.apply(enriched)

    assert result.sub_scores["entry_quality"] == pytest.approx(0.0)
    assert "missing_current_price" in result.evidence.get("errors", [])
