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
            self._map = mapping or {}
    return _Stub(tier_map or {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})


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
