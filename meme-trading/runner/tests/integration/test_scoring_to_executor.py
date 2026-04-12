"""Integration: ScoringEngine -> PaperExecutor -> verify paper_positions + alert_bus."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.executor.paper import PaperExecutor
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
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1", "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
        "position_sizing": {"strong_candidate_sol": 0.25, "probable_runner_sol": 0.375},
        "executor": {"check_interval_sec": 30, "error_closure_hours": 36},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _tier_cache_stub():
    class _Stub(WalletTierCache):
        def __init__(self):
            self._map = {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    return _Stub()


def _enriched():
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=7,
    )
    return EnrichedToken(
        token_mint="MINT1", cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
        price_sol=0.0006, symbol="$TEST", cluster_signal_id=7,
    )


@pytest.mark.asyncio
async def test_scoring_to_executor_end_to_end(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()
    weights = WeightsLoader(_weights_file(tmp_path))
    filtered_bus = asyncio.Queue()
    scored_bus = asyncio.Queue()
    alert_bus = asyncio.Queue()

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=weights, tier_cache=_tier_cache_stub(), db=db,
    )

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {"price_sol": 0.0006, "price_usd": 0.096}

    executor = PaperExecutor(
        scored_bus=scored_bus, alert_bus=alert_bus,
        weights=weights, price_fetcher=price_fetcher, db=db,
    )

    fc = FilteredCandidate(
        enriched=_enriched(),
        filter_results=[
            FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
            FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
            FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
            FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
            FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
        ],
        gate_passed=True, hard_fail_reason=None,
    )

    scored = scoring.score(fc)
    db_id = await scoring._persist(scored)
    scored = replace(scored, runner_score_db_id=db_id)

    await executor._process_one(scored)

    async with db.conn.execute("SELECT * FROM paper_positions WHERE token_mint='MINT1'") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[3] == db_id  # runner_score_id

    alert = alert_bus.get_nowait()
    assert alert["type"] == "runner_entry"
    assert alert["runner_score_id"] == db_id
    assert alert["verdict"] in ("strong_candidate", "probable_runner")
    assert "cluster_summary" in alert
    assert "explanation" in alert

    await db.close()
