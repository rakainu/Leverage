"""WalletMonitor persists BuyEvents to the buy_events table."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from runner.db.database import Database
from runner.ingest.events import BuyEvent
from runner.ingest.wallet_monitor import WalletMonitor


@pytest.mark.asyncio
async def test_persists_emitted_buy_event(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigP1",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.5,
        token_amount=1000.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=db,
    )
    await monitor.handle_signature("sigP1", "W")

    async with db.conn.execute(
        "SELECT signature, wallet_address, token_mint, sol_amount, token_amount, price_sol FROM buy_events"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("sigP1", "W", "MINT1", 0.5, 1000.0, 0.0005)

    await db.close()


@pytest.mark.asyncio
async def test_duplicate_signature_does_not_double_insert(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigDup",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.25,
        token_amount=500.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=db,
    )
    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")

    async with db.conn.execute("SELECT COUNT(*) FROM buy_events") as cur:
        count = (await cur.fetchone())[0]
    assert count == 1

    await db.close()


@pytest.mark.asyncio
async def test_monitor_without_db_still_works(tmp_path):
    """Passing db=None preserves the existing test-mode behavior."""
    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigN",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.25,
        token_amount=500.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=None,
    )
    await monitor.handle_signature("sigN", "W")
    assert bus.qsize() == 1
