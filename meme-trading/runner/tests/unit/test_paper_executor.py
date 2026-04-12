"""PaperExecutor unit tests — real DB, mock price fetcher."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.executor.paper import PaperExecutor
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import ScoredCandidate


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "position_sizing": {"strong_candidate_sol": 0.25, "probable_runner_sol": 0.375},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _signal(signal_id: int = 1) -> ClusterSignal:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=signal_id,
    )


def _enriched(sig: ClusterSignal | None = None) -> EnrichedToken:
    s = sig or _signal()
    return EnrichedToken(
        token_mint=s.token_mint, cluster_signal=s,
        enriched_at=datetime(2026, 4, 12, 10, 15, tzinfo=timezone.utc),
        symbol="TESTSYM", name="Test Token", decimals=9, supply=1e9,
        price_sol=0.0005, price_usd=0.08, liquidity_usd=50000.0,
        cluster_signal_id=s.id,
    )


def _filtered(enriched: EnrichedToken | None = None) -> FilteredCandidate:
    e = enriched or _enriched()
    return FilteredCandidate(
        enriched=e,
        filter_results=[FilterResult(filter_name="rug_pull", passed=True, hard_fail_reason=None)],
        gate_passed=True,
        hard_fail_reason=None,
    )


def _scored_candidate(
    verdict: str = "strong_candidate",
    score: float = 72.0,
    db_id: int | None = 42,
) -> ScoredCandidate:
    return ScoredCandidate(
        filtered=_filtered(),
        runner_score=score,
        verdict=verdict,
        dimension_scores={
            "wallet_quality": 80, "cluster_quality": 70, "entry_quality": 65,
            "holder_quality": 60, "rug_risk": 75, "follow_through": 55, "narrative": 50,
        },
        explanation={"summary": "test candidate"},
        scored_at=datetime(2026, 4, 12, 10, 20, tzinfo=timezone.utc),
        runner_score_db_id=db_id,
    )


async def _setup(tmp_path: Path, enable: bool = True):
    """Create DB, seed runner_scores FK row, return (executor, alert_bus, db)."""
    db = Database(tmp_path / "test.db")
    await db.connect()

    # Seed a runner_scores row so FK passes
    assert db.conn is not None
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id,
           runner_score, verdict, sub_scores_json, explanation_json)
           VALUES (42, 'MINT1', 1, 72.0, 'strong_candidate', '{}', '{}')""",
    )
    await db.conn.commit()

    weights = WeightsLoader(_weights_file(tmp_path))
    scored_bus: asyncio.Queue = asyncio.Queue()
    alert_bus: asyncio.Queue = asyncio.Queue()

    price_fetcher = AsyncMock()
    price_fetcher.fetch = AsyncMock(return_value={"price_sol": 0.0005, "price_usd": 0.08})

    executor = PaperExecutor(
        scored_bus=scored_bus,
        alert_bus=alert_bus,
        weights=weights,
        price_fetcher=price_fetcher,
        db=db,
        enable_executor=enable,
    )
    return executor, alert_bus, db, price_fetcher


@pytest.mark.asyncio
async def test_opens_position_for_strong_candidate(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT amount_sol FROM paper_positions") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.25
    assert not alert_bus.empty()
    alert = await alert_bus.get()
    assert alert["verdict"] == "strong_candidate"
    await db.close()


@pytest.mark.asyncio
async def test_opens_position_for_probable_runner(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    # Need a second runner_scores row for db_id=43
    assert db.conn is not None
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id,
           runner_score, verdict, sub_scores_json, explanation_json)
           VALUES (43, 'MINT1', 1, 85.0, 'probable_runner', '{}', '{}')""",
    )
    await db.conn.commit()

    sc = _scored_candidate(verdict="probable_runner", score=85.0, db_id=43)

    await executor._process_one(sc)

    async with db.conn.execute("SELECT amount_sol FROM paper_positions") as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0.375
    assert not alert_bus.empty()
    alert = await alert_bus.get()
    assert alert["verdict"] == "probable_runner"
    await db.close()


@pytest.mark.asyncio
async def test_skips_ignore_verdict(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="ignore", score=20.0, db_id=42)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_watch_verdict(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="watch", score=45.0, db_id=42)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_when_executor_disabled(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path, enable=False)
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_on_price_fetch_failure(tmp_path):
    executor, alert_bus, db, price_fetcher = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_duplicate_runner_score_id(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42)

    # First insert succeeds
    await executor._process_one(sc)
    assert not alert_bus.empty()
    await alert_bus.get()

    # Second insert with same runner_score_db_id is silently skipped
    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 1
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_does_not_alert_if_no_db_id(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=None)

    await executor._process_one(sc)

    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions") as cur:
        count = (await cur.fetchone())[0]
    assert count == 0
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_entry_alert_has_correct_fields(tmp_path):
    executor, alert_bus, db, _ = await _setup(tmp_path)
    sc = _scored_candidate(verdict="strong_candidate", score=72.0, db_id=42)

    await executor._process_one(sc)

    assert not alert_bus.empty()
    alert = await alert_bus.get()

    expected_keys = {
        "type", "paper_position_id", "runner_score_id", "token_mint", "symbol",
        "verdict", "runner_score", "amount_sol", "entry_price_sol", "entry_price_usd",
        "cluster_summary", "explanation",
    }
    assert set(alert.keys()) == expected_keys
    assert alert["type"] == "runner_entry"
    assert alert["token_mint"] == "MINT1"
    assert alert["symbol"] == "TESTSYM"
    assert alert["runner_score_id"] == 42
    assert alert["amount_sol"] == 0.25
    assert alert["entry_price_sol"] == 0.0005
    assert alert["entry_price_usd"] == 0.08
    assert alert["cluster_summary"]["wallet_count"] == 3
    assert alert["cluster_summary"]["tier_counts"] == {"A": 2, "B": 1}
    assert alert["cluster_summary"]["convergence_minutes"] == 14.0
    assert alert["explanation"] == {"summary": "test candidate"}
    assert isinstance(alert["paper_position_id"], int)
    await db.close()
