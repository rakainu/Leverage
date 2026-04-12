"""WalletRegistryTracker diff-on-reload tests."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tracker import WalletRegistryTracker
from runner.db.database import Database


def _wallets_json(wallets: list[dict]) -> str:
    return json.dumps({"wallets": wallets})


def _wallet(addr, name="w", source="test", active=True):
    return {"address": addr, "name": name, "source": source, "tags": [], "active": active, "added_at": "2026-01-01"}


async def _setup(tmp_path, initial_wallets):
    p = tmp_path / "wallets.json"
    p.write_text(_wallets_json(initial_wallets), encoding="utf-8")
    registry = WalletRegistry(p)
    registry.load()
    db = Database(tmp_path / "r.db")
    await db.connect()
    tracker = WalletRegistryTracker(registry, db, reload_interval_sec=0)
    # Take initial snapshot
    tracker._prev_snapshot = tracker._take_snapshot()
    return tracker, registry, db, p


async def _get_events(db):
    async with db.conn.execute(
        "SELECT wallet_address, action, source, label, detail_json FROM wallet_registry_events ORDER BY id"
    ) as cur:
        return await cur.fetchall()


@pytest.mark.asyncio
async def test_detects_new_wallet_added(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1")])

    # Add a new wallet
    p.write_text(_wallets_json([_wallet("A1"), _wallet("B2", name="new-one", source="gmgn")]), encoding="utf-8")
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 1
    assert events[0][0] == "B2"  # wallet_address
    assert events[0][1] == "added"  # action
    assert events[0][2] == "gmgn"  # source
    assert events[0][3] == "new-one"  # label
    await db.close()


@pytest.mark.asyncio
async def test_detects_wallet_deactivated(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1"), _wallet("B2")])

    # Deactivate B2
    p.write_text(_wallets_json([_wallet("A1"), _wallet("B2", active=False)]), encoding="utf-8")
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 1
    assert events[0][0] == "B2"
    assert events[0][1] == "deactivated"
    await db.close()


@pytest.mark.asyncio
async def test_detects_wallet_reactivated(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1", active=False)])

    # Reactivate A1
    p.write_text(_wallets_json([_wallet("A1", active=True)]), encoding="utf-8")
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 1
    assert events[0][0] == "A1"
    assert events[0][1] == "reactivated"
    await db.close()


@pytest.mark.asyncio
async def test_detects_wallet_removed_from_file(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1"), _wallet("B2")])

    # Remove B2 entirely
    p.write_text(_wallets_json([_wallet("A1")]), encoding="utf-8")
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 1
    assert events[0][0] == "B2"
    assert events[0][1] == "deactivated"
    detail = json.loads(events[0][4])
    assert detail["reason"] == "removed_from_file"
    await db.close()


@pytest.mark.asyncio
async def test_no_events_when_unchanged(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1"), _wallet("B2")])

    # Reload same file — no changes
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 0
    await db.close()


@pytest.mark.asyncio
async def test_multiple_changes_in_one_reload(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1"), _wallet("B2")])

    # Add C3, deactivate B2
    p.write_text(_wallets_json([_wallet("A1"), _wallet("B2", active=False), _wallet("C3", source="nansen")]), encoding="utf-8")
    await tracker._reload_and_diff()

    events = await _get_events(db)
    assert len(events) == 2
    actions = {e[0]: e[1] for e in events}
    assert actions["B2"] == "deactivated"
    assert actions["C3"] == "added"
    await db.close()


@pytest.mark.asyncio
async def test_reload_failure_keeps_previous_snapshot(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1")])

    # Corrupt the file
    p.write_text("not json", encoding="utf-8")
    await tracker._reload_and_diff()  # should not crash

    # Previous snapshot still intact
    assert "A1" in tracker._prev_snapshot
    events = await _get_events(db)
    assert len(events) == 0
    await db.close()


@pytest.mark.asyncio
async def test_last_sync_updated(tmp_path):
    tracker, registry, db, p = await _setup(tmp_path, [_wallet("A1")])
    tracker._last_sync = None

    await tracker._reload_and_diff()
    assert tracker.last_sync is not None
    await db.close()
