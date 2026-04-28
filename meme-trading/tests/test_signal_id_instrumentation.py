"""Regression test for the signal_id instrumentation bug.

Background: as of the 2026-04-27 audit, all 106 closed paper positions had
positions.signal_id = NULL because the paper executor never wrote that column.
Every audit query that needed signal-side attributes (wallet_count, signal_at,
total_amount_sol) had to inverse-JOIN through convergence_signals.position_id.

This test asserts that after PaperExecutor.execute(), the resulting position
row has signal_id pointing back to the originating convergence_signals.id.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest


@pytest.fixture
def smc_db_env(tmp_path, monkeypatch):
    """Point the singleton DB at a temp file and reset module-level state."""
    db_file = tmp_path / "smc_test.db"
    monkeypatch.setenv("SMC_DB_PATH", str(db_file))
    # Reset singleton so it picks up the new path
    import db.database as dbmod
    dbmod._connection = None
    yield db_file
    dbmod._connection = None


@pytest.mark.asyncio
async def test_paper_executor_sets_signal_id_on_position(smc_db_env):
    from config.settings import Settings
    from db.database import get_db, init_db
    from engine.signal import BuyEvent, ConvergenceSignal
    from engine.safety import SafetyResult
    from executor.paper import PaperExecutor

    await init_db()

    db = await get_db()

    # Seed a convergence_signals row that the executor will FK back to
    now = datetime.now(timezone.utc)
    cur = await db.execute(
        """INSERT INTO convergence_signals
           (token_mint, token_symbol, wallet_count, wallets_json,
            first_buy_at, signal_at, avg_amount_sol, total_amount_sol)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("MINT_X", "TOKX", 3, '["w1","w2","w3"]',
         now.isoformat(), now.isoformat(), 1.0, 3.0),
    )
    await db.commit()
    cs_id = cur.lastrowid

    # Build a signal carrying that db_id (the contract _persist_signal sets up)
    signal = ConvergenceSignal(
        token_mint="MINT_X",
        token_symbol="TOKX",
        wallets=["w1", "w2", "w3"],
        buy_events=[],
        first_buy_at=now,
        signal_at=now,
        avg_amount_sol=1.0,
        total_amount_sol=3.0,
        convergence_minutes=8.0,
        db_id=cs_id,
    )
    safety = SafetyResult(passed=True)

    settings = Settings()
    settings.trade_amount_sol = 0.1
    executor = PaperExecutor(settings)

    # Mock Jupiter price call so we don't hit the network
    with patch.object(executor.jupiter, "get_price_sol", new=AsyncMock(return_value=0.000001)):
        position_id = await executor.execute(signal, safety)

    assert position_id is not None

    # The actual assertion: the position row's signal_id must equal cs_id
    row = await (await db.execute(
        "SELECT signal_id FROM positions WHERE id=?", (position_id,)
    )).fetchone()
    assert row is not None, "position row must exist"
    assert row["signal_id"] == cs_id, (
        f"positions.signal_id should equal {cs_id}, got {row['signal_id']}"
    )

    # And the inverse link should be keyed correctly
    inv = await (await db.execute(
        "SELECT position_id, action_taken FROM convergence_signals WHERE id=?",
        (cs_id,),
    )).fetchone()
    assert inv["position_id"] == position_id
    assert inv["action_taken"] == "paper_trade"


@pytest.mark.asyncio
async def test_convergence_engine_sets_db_id_on_signal(smc_db_env):
    """ConvergenceEngine._persist_signal must populate signal.db_id so executors can FK back."""
    from db.database import init_db
    from engine.convergence import ConvergenceEngine
    from engine.signal import ConvergenceSignal

    await init_db()

    # Minimal engine instance — _persist_signal only touches the DB
    engine = ConvergenceEngine.__new__(ConvergenceEngine)
    import logging
    engine._logger = logging.getLogger("test")  # not actually used by _persist_signal

    now = datetime.now(timezone.utc)
    signal = ConvergenceSignal(
        token_mint="MINT_Y",
        token_symbol="TOKY",
        wallets=["a", "b", "c"],
        buy_events=[],
        first_buy_at=now,
        signal_at=now,
        avg_amount_sol=2.0,
        total_amount_sol=6.0,
        convergence_minutes=7.0,
    )

    assert signal.db_id is None
    await engine._persist_signal(signal)
    assert signal.db_id is not None
    assert isinstance(signal.db_id, int)
    assert signal.db_id > 0
