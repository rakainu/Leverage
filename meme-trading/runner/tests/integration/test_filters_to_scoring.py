"""Integration: FilterPipeline → ScoringEngine end-to-end flow."""
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
from runner.filters.base import BaseFilter, FilterResult
from runner.filters.pipeline import FilterPipeline
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


def _tier_cache_stub():
    class _Stub(WalletTierCache):
        def __init__(self):
            self._map = {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    return _Stub()


class _StubFilter(BaseFilter):
    def __init__(self, name: str, result: FilterResult):
        self.name = name
        self._result = result

    async def apply(self, enriched):
        return self._result


def _enriched(mint="MINT1") -> EnrichedToken:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint, wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=7,
    )
    return EnrichedToken(
        token_mint=mint, cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
        price_sol=0.0006, cluster_signal_id=7,
    )


@pytest.mark.asyncio
async def test_filter_to_scoring_end_to_end(tmp_path):
    """Push an enriched token through filters + scoring, verify DB rows."""
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()
    scored_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug_gate", FilterResult("rug_gate", True, None, {"rug_risk": 85.0}, {})),
        _StubFilter("holder_filter", FilterResult("holder_filter", True, None, {"holder_quality": 55.0}, {})),
        _StubFilter("insider_filter", FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {})),
        _StubFilter("entry_quality", FilterResult("entry_quality", True, None, {"entry_quality": 80.0}, {})),
    ]
    probe = _StubFilter("follow_through", FilterResult("follow_through", True, None, {"follow_through": 65.0}, {}))

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus, filtered_bus=filtered_bus,
        sync_filters=filters, probe_filter=probe, db=db,
    )

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    # Push enriched token through pipeline
    await pipeline._process_one(_enriched())

    # Manually run one scoring cycle
    fc = await filtered_bus.get()
    sc = scoring.score(fc)
    await scoring._persist(sc)
    await scored_bus.put(sc)

    # Verify filter_results in DB
    async with db.conn.execute(
        "SELECT COUNT(*) FROM filter_results WHERE token_mint='MINT1'"
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count == 5  # 4 sync + 1 probe

    # Verify runner_scores in DB
    async with db.conn.execute(
        "SELECT runner_score, verdict, cluster_signal_id, short_circuited, sub_scores_json FROM runner_scores WHERE token_mint='MINT1'"
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    assert row[0] > 0  # runner_score > 0
    assert row[1] in ("watch", "strong_candidate", "probable_runner")
    assert row[2] == 7  # cluster_signal_id threaded
    assert row[3] == 0  # not short-circuited

    sub_scores = json.loads(row[4])
    assert len(sub_scores) == 9
    for key in DIMENSION_KEYS:
        assert key in sub_scores

    # Verify scored candidate on bus
    sc_out = scored_bus.get_nowait()
    assert sc_out.runner_score == sc.runner_score

    await db.close()


@pytest.mark.asyncio
async def test_filter_to_scoring_short_circuit(tmp_path):
    """Hard gate failure flows through to scoring as ignore."""
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()
    scored_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug_gate", FilterResult("rug_gate", False, "lp not locked", {"rug_risk": 0}, {})),
        _StubFilter("holder_filter", FilterResult("holder_filter", True, None, {"holder_quality": 55.0}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus, filtered_bus=filtered_bus,
        sync_filters=filters, probe_filter=None, db=db,
    )

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    await pipeline._process_one(_enriched())
    fc = await filtered_bus.get()
    sc = scoring.score(fc)
    await scoring._persist(sc)

    assert sc.runner_score == 0.0
    assert sc.verdict == "ignore"

    async with db.conn.execute(
        "SELECT verdict, short_circuited FROM runner_scores WHERE token_mint='MINT1'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "ignore"
    assert row[1] == 1

    await db.close()
