"""Scoring engine persistence tests — real DB, no queues."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.engine import ScoringEngine
from runner.scoring.models import DIMENSION_KEYS


def _weights_file(tmp_path: Path) -> Path:
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
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _tier_cache_stub(tier_map=None):
    class _Stub(WalletTierCache):
        def __init__(self, mapping):
            self._map = mapping if mapping is not None else {}
    return _Stub(tier_map if tier_map is not None else {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})


def _signal(signal_id=42):
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=signal_id,
    )


def _enriched(sig=None):
    s = sig or _signal()
    return EnrichedToken(
        token_mint=s.token_mint, cluster_signal=s,
        enriched_at=datetime(2026, 4, 12, 10, 15, tzinfo=timezone.utc),
        price_sol=0.0006, cluster_signal_id=s.id,
    )


def _all_pass_results():
    return [
        FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]


@pytest.mark.asyncio
async def test_persist_normal_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    fc = FilteredCandidate(
        enriched=_enriched(), filter_results=_all_pass_results(),
        gate_passed=True, hard_fail_reason=None,
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT * FROM runner_scores WHERE token_mint='MINT1'") as cur:
        row = await cur.fetchone()

    assert row is not None
    # row: id, token_mint, cluster_signal_id, runner_score, verdict, short_circuited, sub_scores_json, explanation_json, created_at
    assert row[1] == "MINT1"
    assert row[2] == 42  # cluster_signal_id
    assert row[4] in ("watch", "strong_candidate", "probable_runner")
    assert row[5] == 0  # short_circuited

    sub_scores = json.loads(row[6])
    assert len(sub_scores) == 9  # 7 dimensions + 2 raw
    assert "raw_rug_risk" in sub_scores
    assert "raw_insider_risk" in sub_scores
    for key in DIMENSION_KEYS:
        assert key in sub_scores

    await db.close()


@pytest.mark.asyncio
async def test_persist_short_circuited_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    fc = FilteredCandidate(
        enriched=_enriched(),
        filter_results=[FilterResult("rug_gate", False, "lp not locked", {"rug_risk": 0}, {})],
        gate_passed=False, hard_fail_reason="lp not locked", hard_fail_filter_name="rug_gate",
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT verdict, short_circuited, explanation_json FROM runner_scores") as cur:
        row = await cur.fetchone()

    assert row[0] == "ignore"
    assert row[1] == 1
    explanation = json.loads(row[2])
    assert explanation["failed_gate"] == "rug_gate"
    assert explanation["short_circuited"] is True

    await db.close()


@pytest.mark.asyncio
async def test_persist_cluster_signal_id_threaded(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    sig = _signal(signal_id=99)
    fc = FilteredCandidate(
        enriched=_enriched(sig), filter_results=_all_pass_results(),
        gate_passed=True, hard_fail_reason=None,
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT cluster_signal_id FROM runner_scores") as cur:
        row = await cur.fetchone()

    assert row[0] == 99

    await db.close()
