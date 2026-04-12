"""ScoredCandidate model tests."""
from datetime import datetime, timedelta, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS, ScoredCandidate, Verdict


def _make_scored() -> ScoredCandidate:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="MINT1",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14),
        convergence_seconds=840,
        mid_price_sol=0.0005,
    )
    enriched = EnrichedToken(
        token_mint="MINT1",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
    )
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=[],
        gate_passed=True,
        hard_fail_reason=None,
    )
    return ScoredCandidate(
        filtered=fc,
        runner_score=65.3,
        verdict="strong_candidate",
        dimension_scores={k: 50.0 for k in DIMENSION_KEYS},
        explanation={"short_circuited": False},
        scored_at=base + timedelta(minutes=16),
    )


def test_scored_candidate_is_frozen():
    sc = _make_scored()
    assert sc.runner_score == 65.3
    assert sc.verdict == "strong_candidate"
    try:
        sc.runner_score = 99.0
        assert False, "should have raised"
    except AttributeError:
        pass


def test_dimension_keys_has_seven_entries():
    assert len(DIMENSION_KEYS) == 7
    assert "wallet_quality" in DIMENSION_KEYS
    assert "cluster_quality" in DIMENSION_KEYS
    assert "entry_quality" in DIMENSION_KEYS
    assert "holder_quality" in DIMENSION_KEYS
    assert "rug_risk" in DIMENSION_KEYS
    assert "follow_through" in DIMENSION_KEYS
    assert "narrative" in DIMENSION_KEYS


def test_verdict_type_accepts_valid_values():
    valid: list[Verdict] = ["ignore", "watch", "strong_candidate", "probable_runner"]
    assert len(valid) == 4
