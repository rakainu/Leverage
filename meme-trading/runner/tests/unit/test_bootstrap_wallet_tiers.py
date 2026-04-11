"""Bootstrap script seeds wallet_tiers from wallets.json as Tier A."""
import json
from pathlib import Path

import pytest

from runner.db.database import Database
from runner.scripts.bootstrap_wallet_tiers import bootstrap_wallet_tiers


@pytest.fixture
def wallets_json(tmp_path: Path) -> Path:
    p = tmp_path / "wallets.json"
    p.write_text(
        json.dumps(
            {
                "wallets": [
                    {
                        "address": "W1",
                        "name": "active-1",
                        "source": "nansen",
                        "active": True,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "address": "W2",
                        "name": "active-2",
                        "source": "gmgn",
                        "active": True,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "address": "W3",
                        "name": "inactive",
                        "source": "manual",
                        "active": False,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                ]
            }
        )
    )
    return p


@pytest.mark.asyncio
async def test_bootstrap_inserts_active_wallets_as_tier_a(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    count = await bootstrap_wallet_tiers(db, wallets_json)

    assert count == 2

    async with db.conn.execute(
        "SELECT wallet_address, tier, source FROM wallet_tiers ORDER BY wallet_address"
    ) as cur:
        rows = await cur.fetchall()

    assert rows == [
        ("W1", "A", "manual_bootstrap"),
        ("W2", "A", "manual_bootstrap"),
    ]
    await db.close()


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await bootstrap_wallet_tiers(db, wallets_json)
    # Second run should not duplicate rows
    count = await bootstrap_wallet_tiers(db, wallets_json)
    assert count == 2

    async with db.conn.execute("SELECT COUNT(*) FROM wallet_tiers") as cur:
        total = (await cur.fetchone())[0]
    assert total == 2

    await db.close()


@pytest.mark.asyncio
async def test_bootstrap_does_not_downgrade_existing_non_u_tiers(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    # Pre-seed W1 as Tier B
    await db.conn.execute(
        "INSERT INTO wallet_tiers (wallet_address, tier, source) VALUES ('W1', 'B', 'rebuilder')"
    )
    await db.conn.commit()

    await bootstrap_wallet_tiers(db, wallets_json)

    async with db.conn.execute(
        "SELECT tier, source FROM wallet_tiers WHERE wallet_address = 'W1'"
    ) as cur:
        row = await cur.fetchone()
    # W1 should still be B — bootstrap only fills in missing
    assert row == ("B", "rebuilder")

    await db.close()
