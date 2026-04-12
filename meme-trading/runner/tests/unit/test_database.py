"""Database singleton creates schema, enables WAL, returns aiosqlite connection."""
from pathlib import Path

import pytest

from runner.db.database import Database


@pytest.mark.asyncio
async def test_database_creates_tables(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        rows = await cur.fetchall()
    names = {r[0] for r in rows}

    for expected in [
        "buy_events",
        "wallet_tiers",
        "wallet_trades",
        "cluster_signals",
        "schema_version",
    ]:
        assert expected in names, f"missing table {expected}"

    await db.close()


@pytest.mark.asyncio
async def test_database_enables_wal(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
    assert row[0].lower() == "wal"

    await db.close()


@pytest.mark.asyncio
async def test_insert_and_query_buy_event(tmp_path: Path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.execute(
        """
        INSERT INTO buy_events
        (signature, wallet_address, token_mint, sol_amount,
         token_amount, price_sol, block_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("sig1", "wallet1", "mint1", 0.5, 1000, 0.0005, "2026-04-11T10:00:00Z"),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT signature, wallet_address FROM buy_events WHERE signature = ?",
        ("sig1",),
    ) as cur:
        row = await cur.fetchone()
    assert row == ("sig1", "wallet1")

    await db.close()


@pytest.mark.asyncio
async def test_database_is_idempotent_on_reconnect(tmp_path: Path):
    p = tmp_path / "r.db"
    db1 = Database(p)
    await db1.connect()
    await db1.close()

    db2 = Database(p)
    await db2.connect()
    async with db2.conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count >= 5
    await db2.close()


@pytest.mark.asyncio
async def test_migration_adds_short_circuited_column(tmp_path):
    """Existing runner_scores table without short_circuited gets the column on migration."""
    db_path = tmp_path / "migrate.db"

    # Create the old schema without short_circuited
    import aiosqlite
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("""
        CREATE TABLE runner_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint TEXT NOT NULL,
            cluster_signal_id INTEGER,
            runner_score REAL NOT NULL,
            verdict TEXT NOT NULL,
            sub_scores_json TEXT NOT NULL,
            explanation_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.commit()
    await conn.close()

    # Now open with Database which should run migration
    from runner.db.database import Database
    db = Database(db_path)
    await db.connect()

    # Verify the column exists by inserting a row that uses it
    await db.conn.execute(
        """INSERT INTO runner_scores
           (token_mint, runner_score, verdict, short_circuited, sub_scores_json, explanation_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("MINT1", 0.0, "ignore", 1, "{}", "{}"),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT short_circuited FROM runner_scores WHERE token_mint = 'MINT1'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1

    await db.close()
