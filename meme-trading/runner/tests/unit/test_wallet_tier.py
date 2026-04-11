"""Wallet tier cache — loads from DB, defaults unknown → U."""
import pytest

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database


@pytest.mark.asyncio
async def test_loads_tiers_from_db(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.executemany(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES (?,?,?,?)",
        [
            ("WA", "A", 0.70, 10),
            ("WB", "B", 0.45, 6),
            ("WC", "C", 0.20, 8),
        ],
    )
    await db.conn.commit()

    cache = WalletTierCache(db)
    await cache.load()

    assert cache.tier_of("WA") == Tier.A
    assert cache.tier_of("WB") == Tier.B
    assert cache.tier_of("WC") == Tier.C
    assert cache.tier_of("unknown") == Tier.U  # default for no record

    await db.close()


@pytest.mark.asyncio
async def test_tier_points_mapping(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()
    cache = WalletTierCache(db)
    await cache.load()

    assert Tier.A.points == 100
    assert Tier.B.points == 60
    assert Tier.C.points == 0
    assert Tier.U.points == 40

    await db.close()


@pytest.mark.asyncio
async def test_reload_picks_up_changes(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    cache = WalletTierCache(db)
    await cache.load()
    assert cache.tier_of("late_wallet") == Tier.U

    await db.conn.execute(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES ('late_wallet','A',0.8,10)"
    )
    await db.conn.commit()

    await cache.load()
    assert cache.tier_of("late_wallet") == Tier.A

    await db.close()


@pytest.mark.asyncio
async def test_counts_a_b_wallets_in_list(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.executemany(
        "INSERT INTO wallet_tiers (wallet_address, tier, win_rate, trade_count) VALUES (?,?,?,?)",
        [("A1", "A", 0.7, 10), ("B1", "B", 0.5, 6), ("C1", "C", 0.2, 8)],
    )
    await db.conn.commit()

    cache = WalletTierCache(db)
    await cache.load()

    count = cache.count_ab(["A1", "B1", "C1", "unknown_wallet"])
    assert count == 2  # A1 + B1 only (C1 and unknown excluded)

    await db.close()
