"""Wallet monitor: dedup, parse, emit to bus."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from runner.ingest.events import BuyEvent
from runner.ingest.wallet_monitor import WalletMonitor


@pytest.mark.asyncio
async def test_emits_buy_event_when_parser_returns_one():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigA",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigA", "W")

    ev = bus.get_nowait()
    assert ev.signature == "sigA"
    assert parser.parse_transaction.call_count == 1


@pytest.mark.asyncio
async def test_ignores_duplicate_signature():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigDup",
        wallet_address="W",
        token_mint="M",
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=datetime(2026, 4, 11, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")

    assert bus.qsize() == 1
    assert parser.parse_transaction.call_count == 1


@pytest.mark.asyncio
async def test_skips_unknown_wallet():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()

    monitor = WalletMonitor(
        wallets={"KnownWallet": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigX", "UnknownWallet")

    assert bus.empty()
    assert parser.parse_transaction.call_count == 0


@pytest.mark.asyncio
async def test_non_buy_transaction_does_not_emit():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = None  # parser says: not a buy

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
    )

    await monitor.handle_signature("sigN", "W")

    assert bus.empty()


@pytest.mark.asyncio
async def test_seen_cache_is_bounded():
    bus: asyncio.Queue[BuyEvent] = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = None

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        max_seen=50,
    )

    for i in range(120):
        await monitor.handle_signature(f"sig{i}", "W")

    assert len(monitor._seen_signatures) <= 50
