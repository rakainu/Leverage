"""MilestoneSnapshotter unit tests — real DB, mock price fetcher."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from runner.db.database import Database
from runner.executor.snapshotter import MilestoneSnapshotter


async def _setup(tmp_path: Path, price_sol: float = 0.0008):
    db = Database(tmp_path / "test.db")
    await db.connect()
    alert_bus = asyncio.Queue()
    price_fetcher = AsyncMock()
    price_fetcher.fetch = AsyncMock(return_value={"price_sol": price_sol, "price_usd": 0.12})
    snap = MilestoneSnapshotter(
        alert_bus=alert_bus,
        price_fetcher=price_fetcher,
        db=db,
        check_interval_sec=0,
    )
    return db, alert_bus, price_fetcher, snap


async def _insert_position(
    db: Database,
    signal_time: datetime,
    entry_price: float = 0.0006,
    position_id: int = 1,
    score_id: int = 42,
):
    assert db.conn is not None
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id, runner_score,
           verdict, sub_scores_json, explanation_json)
           VALUES (?, 'MINT1', 1, 72.0, 'strong_candidate', '{}', '{}')""",
        (score_id,),
    )
    await db.conn.execute(
        """INSERT INTO paper_positions
           (id, token_mint, symbol, runner_score_id, verdict, runner_score,
            entry_price_sol, entry_price_usd, amount_sol, signal_time, status)
           VALUES (?, 'MINT1', 'TEST', ?, 'strong_candidate', 72.0,
                   ?, 0.09, 0.25, ?, 'open')""",
        (position_id, score_id, entry_price, signal_time.isoformat()),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_captures_5m_milestone(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT price_5m_sol, pnl_5m_pct FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == pytest.approx(0.0008)
    assert row[1] == pytest.approx(33.333, rel=0.01)
    await db.close()


@pytest.mark.asyncio
async def test_first_write_only(tmp_path):
    db, alert_bus, price_fetcher, snap = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    # First run: price = 0.0008
    await snap._check_all()

    # Second run: different price
    price_fetcher.fetch.return_value = {"price_sol": 0.0012, "price_usd": 0.18}
    await snap._check_all()

    async with db.conn.execute(
        "SELECT price_5m_sol FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    # Should still be the first value
    assert row[0] == pytest.approx(0.0008)
    await db.close()


@pytest.mark.asyncio
async def test_updates_mfe_mae(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path, price_sol=0.0008)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT max_favorable_pct FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    # (0.0008 - 0.0006) / 0.0006 * 100 = 33.33%
    assert row[0] > 30
    await db.close()


@pytest.mark.asyncio
async def test_mae_is_negative_for_drawdowns(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path, price_sol=0.0004)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT max_adverse_pct FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    # (0.0004 - 0.0006) / 0.0006 * 100 = -33.33%
    assert row[0] < 0
    await db.close()


@pytest.mark.asyncio
async def test_closes_at_24h(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(hours=25)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT status, close_reason FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "closed"
    assert row[1] == "completed"
    assert not alert_bus.empty()
    alert = await alert_bus.get()
    assert alert["type"] == "runner_close"
    await db.close()


@pytest.mark.asyncio
async def test_skips_on_price_fetch_failure(tmp_path):
    db, alert_bus, price_fetcher, snap = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT price_5m_sol FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] is None
    await db.close()


@pytest.mark.asyncio
async def test_error_closure_at_36h(tmp_path):
    db, alert_bus, price_fetcher, snap = await _setup(tmp_path)
    price_fetcher.fetch.return_value = None
    signal_time = datetime.now(timezone.utc) - timedelta(hours=37)
    await _insert_position(db, signal_time)

    await snap._check_all()

    async with db.conn.execute(
        "SELECT status, close_reason, notes_json FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "closed"
    assert row[1] == "error"
    notes = json.loads(row[2])
    assert "persistent_price_failures" in notes["error_closure_reason"]
    # No alert emitted for error closure
    assert alert_bus.empty()
    await db.close()


@pytest.mark.asyncio
async def test_skips_corrupted_entry_price(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    await _insert_position(db, signal_time, entry_price=0)

    # Should not crash
    await snap._check_all()

    async with db.conn.execute(
        "SELECT price_5m_sol FROM paper_positions WHERE id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] is None
    await db.close()


@pytest.mark.asyncio
async def test_close_alert_has_milestones(tmp_path):
    db, alert_bus, _, snap = await _setup(tmp_path)
    signal_time = datetime.now(timezone.utc) - timedelta(hours=25)
    await _insert_position(db, signal_time)

    await snap._check_all()

    alert = await alert_bus.get()
    assert alert["type"] == "runner_close"
    assert "milestones" in alert
    assert "5m" in alert["milestones"]
    assert "24h" in alert["milestones"]
    assert "max_favorable_pct" in alert
    assert "max_adverse_pct" in alert
    assert alert["paper_position_id"] == 1
    assert alert["runner_score_id"] == 42
    assert alert["symbol"] == "TEST"
    await db.close()
