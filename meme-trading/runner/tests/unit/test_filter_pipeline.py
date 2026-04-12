"""FilterPipeline orchestrator — spawns per-candidate tasks, persists results."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.filters.pipeline import FilterPipeline


def _enriched(mint="M") -> EnrichedToken:
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


class _StubFilter(BaseFilter):
    def __init__(self, name: str, result: FilterResult):
        self.name = name  # type: ignore[misc]
        self._result = result

    async def apply(self, enriched):
        return self._result


@pytest.mark.asyncio
async def test_pipeline_runs_all_filters_on_pass(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("f1", FilterResult("f1", True, None, {"a": 50}, {})),
        _StubFilter("f2", FilterResult("f2", True, None, {"b": 60}, {})),
        _StubFilter("f3", FilterResult("f3", True, None, {"c": 70}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    # Call _process_one directly for synchronous verification
    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert fc.gate_passed is True
    assert len(fc.filter_results) == 3
    names = [r.filter_name for r in fc.filter_results]
    assert names == ["f1", "f2", "f3"]

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_short_circuits_on_hard_fail(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("f1", FilterResult("f1", True, None, {"a": 50}, {})),
        _StubFilter("f2", FilterResult("f2", False, "bad thing", {}, {})),
        _StubFilter("f3", FilterResult("f3", True, None, {"c": 70}, {})),  # should not run
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert fc.gate_passed is False
    assert fc.hard_fail_reason == "bad thing"
    assert fc.hard_fail_filter_name == "f2"
    assert len(fc.filter_results) == 2  # f1 + f2
    assert fc.filter_results[-1].filter_name == "f2"

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_runs_probe_after_sync_filters(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    sync_filters = [
        _StubFilter("rug", FilterResult("rug", True, None, {"rug_risk": 90}, {})),
    ]
    probe = _StubFilter(
        "follow_through",
        FilterResult("follow_through", True, None, {"follow_through": 100}, {}),
    )

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=sync_filters,
        probe_filter=probe,
        db=db,
    )

    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert len(fc.filter_results) == 2
    assert fc.filter_results[0].filter_name == "rug"
    assert fc.filter_results[1].filter_name == "follow_through"

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_skips_probe_on_hard_fail(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    sync_filters = [
        _StubFilter("rug", FilterResult("rug", False, "rug fail", {}, {})),
    ]
    probe_called = False

    class _ProbeSpy(BaseFilter):
        name = "probe_spy"

        async def apply(self, enriched):
            nonlocal probe_called
            probe_called = True
            return FilterResult("probe_spy", True, None, {}, {})

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=sync_filters,
        probe_filter=_ProbeSpy(),
        db=db,
    )

    await pipeline._process_one(_enriched())

    assert probe_called is False
    fc = filtered_bus.get_nowait()
    assert fc.gate_passed is False

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_persists_filter_results_to_db(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug", FilterResult("rug", True, None, {"rug_risk": 88}, {"ev": 1})),
        _StubFilter("holder", FilterResult("holder", True, None, {"holder_quality": 70}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    await pipeline._process_one(_enriched())

    async with db.conn.execute(
        "SELECT filter_name, passed, sub_scores_json FROM filter_results ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 2
    assert rows[0][0] == "rug"
    assert json.loads(rows[0][2]) == {"rug_risk": 88}
    assert rows[1][0] == "holder"

    await db.close()
