"""Pure scoring engine unit tests — no DB, no queues."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS


# ──── helpers ────────────────────────────────────────────────────────

def _weights_file(tmp_path: Path, overrides: dict | None = None) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
    }
    if overrides:
        for dotted_key, val in overrides.items():
            parts = dotted_key.split(".")
            node = data
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = val
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _cluster_signal(wallets=("A1", "A2", "B1"), convergence_seconds=840, mid_price=0.0005):
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT1", wallets=list(wallets), wallet_count=len(wallets),
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(seconds=convergence_seconds),
        convergence_seconds=convergence_seconds, mid_price_sol=mid_price, id=42,
    )


def _enriched(signal=None):
    sig = signal or _cluster_signal()
    return EnrichedToken(
        token_mint=sig.token_mint, cluster_signal=sig,
        enriched_at=datetime(2026, 4, 12, 10, 15, tzinfo=timezone.utc),
        price_sol=0.0006, cluster_signal_id=sig.id,
    )


def _all_pass_results():
    return [
        FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]


def _filtered(gate_passed=True, hard_fail_reason=None, hard_fail_filter_name=None, results=None, signal=None):
    enriched = _enriched(signal)
    return FilteredCandidate(
        enriched=enriched,
        filter_results=results if results is not None else _all_pass_results(),
        gate_passed=gate_passed, hard_fail_reason=hard_fail_reason,
        hard_fail_filter_name=hard_fail_filter_name,
    )


def _tier_cache_stub(tier_map=None):
    class _Stub(WalletTierCache):
        def __init__(self, mapping):
            self._map = mapping if mapping is not None else {}
    default = {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    return _Stub(default if tier_map is None else tier_map)


def _engine(tmp_path, tier_map=None, weight_overrides=None):
    from runner.scoring.engine import ScoringEngine
    weights = WeightsLoader(_weights_file(tmp_path, weight_overrides))
    tier_cache = _tier_cache_stub(tier_map)
    return ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=weights, tier_cache=tier_cache,
    )


# ──── verdict assignment ─────────────────────────────────────────────

def test_verdict_ignore(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(0.0) == "ignore"
    assert eng._assign_verdict(39.9) == "ignore"


def test_verdict_watch(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(40.0) == "watch"
    assert eng._assign_verdict(59.9) == "watch"


def test_verdict_strong_candidate(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(60.0) == "strong_candidate"
    assert eng._assign_verdict(77.9) == "strong_candidate"


def test_verdict_probable_runner(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(78.0) == "probable_runner"
    assert eng._assign_verdict(100.0) == "probable_runner"


# ──── score combination ──────────────────────────────────────────────

def test_combine_scores_weighted_sum(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: 50.0 for k in DIMENSION_KEYS}
    assert eng._combine_scores(dims) == pytest.approx(50.0)


def test_combine_scores_clamped_to_100(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: 200.0 for k in DIMENSION_KEYS}
    assert eng._combine_scores(dims) == 100.0


def test_combine_scores_clamped_to_0(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: -50.0 for k in DIMENSION_KEYS}
    assert eng._combine_scores(dims) == 0.0


# ──── weight validation ──────────────────────────────────────────────

def test_validate_weights_passes_for_valid_weights(tmp_path):
    eng = _engine(tmp_path)
    eng._validate_weights()  # should not raise


def test_validate_weights_warns_on_bad_sum(tmp_path):
    with pytest.raises(ValueError, match="sum"):
        _engine(tmp_path, weight_overrides={"weights.narrative": 0.50})


# ──── dimension derivation ───────────────────────────────────────

def test_direct_filter_lookup(tmp_path):
    """entry_quality, holder_quality, follow_through read from filter results."""
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["entry_quality"] == 75.0
    assert dims["holder_quality"] == 60.0
    assert dims["follow_through"] == 60.0


def test_missing_sub_score_uses_neutral_fallback(tmp_path):
    """Missing filter result falls back to neutral_fallback (50)."""
    eng = _engine(tmp_path)
    results = [FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {})]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    assert dims["entry_quality"] == 50.0
    assert dims["holder_quality"] == 50.0
    assert dims["follow_through"] == 50.0


def test_rug_risk_weighted_average(tmp_path):
    """rug_risk = 0.7 * rug + 0.3 * insider."""
    eng = _engine(tmp_path)
    fc = _filtered()  # rug=80, insider=70
    dims = eng._derive_dimensions(fc)
    expected = 0.70 * 80.0 + 0.30 * 70.0  # 77
    assert dims["rug_risk"] == pytest.approx(expected)


def test_rug_risk_insider_cap(tmp_path):
    """Insider < 25 caps combined rug_risk at 35."""
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 20.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    assert dims["rug_risk"] == 35.0


def test_rug_risk_no_cap_when_insider_above_threshold(tmp_path):
    """Insider >= 25 does not trigger cap."""
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 30.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    expected = 0.70 * 90.0 + 0.30 * 30.0  # 72
    assert dims["rug_risk"] == pytest.approx(expected)


def test_wallet_quality_mixed_tiers(tmp_path):
    """A(100) + A(100) + B(60) → mean = 86.67."""
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    expected = mean([100, 100, 60])
    assert dims["wallet_quality"] == pytest.approx(expected, abs=0.1)


def test_wallet_quality_all_a_tier(tmp_path):
    eng = _engine(tmp_path, tier_map={"A1": Tier.A, "A2": Tier.A, "B1": Tier.A})
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["wallet_quality"] == 100.0


def test_wallet_quality_all_u_tier(tmp_path):
    eng = _engine(tmp_path, tier_map={})
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["wallet_quality"] == 40.0


def test_wallet_quality_unknown_wallets(tmp_path):
    """Wallets not in tier cache default to U(40). No crash."""
    eng = _engine(tmp_path, tier_map={"A1": Tier.A})
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    expected = mean([100, 40, 40])
    assert dims["wallet_quality"] == pytest.approx(expected, abs=0.1)


def test_cluster_quality_sweet_spot(tmp_path):
    """14 min convergence (in 10-20 sweet spot) + 3 wallets → 50+0+20=70."""
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 70.0


def test_cluster_quality_fast_convergence_penalty(tmp_path):
    """< 5 min convergence gets -20 penalty → 50+0-20=30."""
    eng = _engine(tmp_path)
    sig = _cluster_signal(convergence_seconds=180)
    fc = _filtered(signal=sig)
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 30.0


def test_cluster_quality_extra_wallets(tmp_path):
    """6 wallets → +30 bonus (capped). 14 min → +20. Total = 50+30+20=100."""
    eng = _engine(tmp_path)
    sig = _cluster_signal(wallets=["A1", "A2", "B1", "B2", "B3", "B4"])
    fc = _filtered(signal=sig)
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 100.0


def test_narrative_is_50(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["narrative"] == 50.0


# ──── short-circuit ──────────────────────────────────────────────

def test_short_circuit_produces_ignore(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(
        gate_passed=False, hard_fail_reason="mint authority not revoked",
        hard_fail_filter_name="rug_gate",
        results=[FilterResult("rug_gate", False, "mint authority not revoked", {"rug_risk": 0}, {})],
    )
    sc = eng.score(fc)
    assert sc.runner_score == 0.0
    assert sc.verdict == "ignore"
    assert all(v == 0.0 for v in sc.dimension_scores.values())
    assert len(sc.dimension_scores) == 7
    assert sc.explanation["short_circuited"] is True
    assert sc.explanation["failed_gate"] == "rug_gate"
    assert sc.explanation["failed_reason"] == "mint authority not revoked"


def test_short_circuit_all_dimension_keys_present(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(gate_passed=False, hard_fail_reason="bad", hard_fail_filter_name="rug_gate",
                   results=[FilterResult("rug_gate", False, "bad", {}, {})])
    sc = eng.score(fc)
    for key in DIMENSION_KEYS:
        assert key in sc.dimension_scores
        assert key in sc.explanation["dimensions"]


# ──── explanation structure ──────────────────────────────────────

def test_explanation_has_version_markers(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    assert sc.explanation["scoring_version"] == "v1"
    assert "weights_mtime" in sc.explanation
    assert "weights_hash" in sc.explanation
    assert len(sc.explanation["weights_hash"]) == 6


def test_explanation_dimensions_have_required_keys(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    for key in DIMENSION_KEYS:
        dim = sc.explanation["dimensions"][key]
        assert "score" in dim
        assert "weight" in dim
        assert "weighted" in dim
        assert "detail" in dim


def test_explanation_verdict_thresholds_present(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    vt = sc.explanation["verdict_thresholds"]
    assert vt == {"watch": 40, "strong_candidate": 60, "probable_runner": 78}


def test_explanation_data_degraded_on_missing_scores(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(results=[])
    sc = eng.score(fc)
    assert sc.explanation["data_degraded"] is True
    assert len(sc.explanation["missing_subscores"]) > 0


def test_explanation_rug_detail_insider_capped(tmp_path):
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 20.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    sc = eng.score(fc)
    rug_detail = sc.explanation["dimensions"]["rug_risk"]["detail"]
    assert rug_detail["insider_capped"] is True
    assert rug_detail["raw_rug"] == 90.0
    assert rug_detail["raw_insider"] == 20.0


# ──── full end-to-end score ──────────────────────────────────────

def test_full_score_known_inputs(tmp_path):
    """Verify the complete pipeline with known inputs produces expected result."""
    eng = _engine(tmp_path)
    fc = _filtered()
    sc = eng.score(fc)

    # wallet_quality = mean(100, 100, 60) = 86.67
    # cluster_quality = 50 + 0 + 20 = 70 (14 min sweet spot, 3 wallets)
    # entry_quality = 75
    # holder_quality = 60
    # rug_risk = 0.7*80 + 0.3*70 = 77
    # follow_through = 60
    # narrative = 50
    # score = 0.20*86.67 + 0.15*70 + 0.15*75 + 0.15*60 + 0.15*77 + 0.15*60 + 0.05*50
    #        = 17.33 + 10.5 + 11.25 + 9.0 + 11.55 + 9.0 + 2.5 = 71.13
    assert sc.runner_score == pytest.approx(71.13, abs=0.5)
    assert sc.verdict == "strong_candidate"
    assert len(sc.dimension_scores) == 7
