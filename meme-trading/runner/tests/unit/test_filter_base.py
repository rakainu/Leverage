"""FilterResult, FilteredCandidate, and BaseFilter contract tests."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult


def _enriched(mint="MINT") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


def test_filter_result_pass_has_no_hard_fail_reason():
    r = FilterResult(
        filter_name="test",
        passed=True,
        hard_fail_reason=None,
        sub_scores={"score": 80.0},
        evidence={"key": "value"},
    )
    assert r.passed is True
    assert r.hard_fail_reason is None
    assert r.sub_scores["score"] == 80.0


def test_filter_result_hard_fail_has_reason():
    r = FilterResult(
        filter_name="rug_gate",
        passed=False,
        hard_fail_reason="mint authority not revoked",
        sub_scores={},
        evidence={"mint_authority": "SomeAddr"},
    )
    assert r.passed is False
    assert r.hard_fail_reason == "mint authority not revoked"


def test_filter_result_is_frozen():
    import dataclasses
    r = FilterResult(
        filter_name="t",
        passed=True,
        hard_fail_reason=None,
        sub_scores={},
        evidence={},
    )
    try:
        r.passed = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("FilterResult must be frozen")


def test_filtered_candidate_carries_enriched_and_results():
    enriched = _enriched()
    results = [
        FilterResult("a", True, None, {"x": 50.0}, {}),
        FilterResult("b", True, None, {"y": 60.0}, {}),
    ]
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=results,
        gate_passed=True,
        hard_fail_reason=None,
    )
    assert fc.enriched.token_mint == "MINT"
    assert len(fc.filter_results) == 2
    assert fc.gate_passed is True


def test_filtered_candidate_hard_fail_shortcircuit():
    enriched = _enriched()
    fail = FilterResult(
        filter_name="rug_gate",
        passed=False,
        hard_fail_reason="lp not locked",
        sub_scores={},
        evidence={"lp_locked_pct": 30},
    )
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=[fail],
        gate_passed=False,
        hard_fail_reason="lp not locked",
    )
    assert fc.gate_passed is False
    assert fc.hard_fail_reason == "lp not locked"


@pytest.mark.asyncio
async def test_base_filter_apply_is_abstract():
    class Incomplete(BaseFilter):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_base_filter_concrete_subclass_works():
    class Stub(BaseFilter):
        name = "stub"

        async def apply(self, enriched: EnrichedToken) -> FilterResult:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"stub_score": 100.0},
                evidence={"mint": enriched.token_mint},
            )

    stub = Stub()
    result = await stub.apply(_enriched())
    assert result.filter_name == "stub"
    assert result.sub_scores["stub_score"] == 100.0
    assert result.evidence["mint"] == "MINT"
