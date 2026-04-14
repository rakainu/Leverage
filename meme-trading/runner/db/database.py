"""aiosqlite singleton for runner.db with WAL mode and schema bootstrap."""
from pathlib import Path

import aiosqlite

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    """Simple async SQLite wrapper.

    Bootstraps the schema on connect, enables WAL mode, exposes the
    underlying aiosqlite connection as `.conn` for callers.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        await self.conn.execute("PRAGMA journal_mode=WAL;")
        await self.conn.execute("PRAGMA synchronous=NORMAL;")
        await self.conn.execute("PRAGMA foreign_keys=ON;")
        await self.conn.commit()
        await self._ensure_schema()

    async def _ensure_schema(self) -> None:
        assert self.conn is not None
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self.conn.executescript(schema_sql)
        await self.conn.commit()
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        """Apply schema migrations for columns added after initial table creation."""
        assert self.conn is not None
        # Migration 1: add short_circuited to runner_scores (Plan 2c)
        async with self.conn.execute("PRAGMA table_info(runner_scores)") as cur:
            columns = {row[1] async for row in cur}
        if "short_circuited" not in columns:
            await self.conn.execute(
                "ALTER TABLE runner_scores ADD COLUMN short_circuited INTEGER DEFAULT 0"
            )
            await self.conn.commit()

        # Migration 2: expand paper_positions.close_reason CHECK to include
        # exit-policy values (stopped_out, trail_stop, trail_breakeven_floor, time_stop).
        async with self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='paper_positions'"
        ) as cur:
            row = await cur.fetchone()
        existing_sql = row[0] if row else ""
        needs_paper_rebuild = existing_sql and (
            "stopped_out" not in existing_sql
            or "trail_breakeven_floor" not in existing_sql
        )
        if needs_paper_rebuild:
            await self.conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN;
                ALTER TABLE paper_positions RENAME TO paper_positions__old;
                CREATE TABLE paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_mint TEXT NOT NULL,
                    symbol TEXT,
                    runner_score_id INTEGER NOT NULL REFERENCES runner_scores(id),
                    verdict TEXT NOT NULL,
                    runner_score REAL NOT NULL,
                    entry_price_sol REAL NOT NULL,
                    entry_price_usd REAL,
                    amount_sol REAL NOT NULL,
                    signal_time TIMESTAMP NOT NULL,
                    entry_source TEXT NOT NULL DEFAULT 'paper_executor_v1',
                    price_5m_sol REAL, pnl_5m_pct REAL,
                    price_30m_sol REAL, pnl_30m_pct REAL,
                    price_1h_sol REAL, pnl_1h_pct REAL,
                    price_4h_sol REAL, pnl_4h_pct REAL,
                    price_24h_sol REAL, pnl_24h_pct REAL,
                    max_favorable_pct REAL DEFAULT 0.0,
                    max_adverse_pct REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
                    close_reason TEXT CHECK (close_reason IN
                        ('completed', 'error', 'stopped_out', 'trail_stop',
                         'trail_breakeven_floor', 'time_stop')),
                    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closed_at TIMESTAMP,
                    notes_json TEXT,
                    UNIQUE(runner_score_id)
                );
                INSERT INTO paper_positions SELECT * FROM paper_positions__old;
                DROP TABLE paper_positions__old;
                CREATE INDEX IF NOT EXISTS idx_paper_positions_mint ON paper_positions(token_mint);
                CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
                CREATE INDEX IF NOT EXISTS idx_paper_positions_verdict ON paper_positions(verdict);
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )
            await self.conn.commit()

        # Migration 3: add `source_stage` column + expand wallet_tiers CHECK
        # to include 'S' (shadow) tier for GMGN vetting funnel.
        async with self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='wallet_tiers'"
        ) as cur:
            row = await cur.fetchone()
        wt_sql = row[0] if row else ""
        needs_wt_rebuild = wt_sql and (
            "'S'" not in wt_sql or "source_stage" not in wt_sql
        )
        if needs_wt_rebuild:
            await self.conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN;
                ALTER TABLE wallet_tiers RENAME TO wallet_tiers__old;
                CREATE TABLE wallet_tiers (
                    wallet_address TEXT PRIMARY KEY,
                    tier TEXT NOT NULL CHECK (tier IN ('A', 'B', 'C', 'S', 'U')),
                    win_rate REAL,
                    trade_count INTEGER DEFAULT 0,
                    pnl_sol REAL DEFAULT 0,
                    source TEXT,
                    source_stage TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO wallet_tiers
                    (wallet_address, tier, win_rate, trade_count, pnl_sol, source, updated_at)
                    SELECT wallet_address, tier, win_rate, trade_count, pnl_sol, source, updated_at
                    FROM wallet_tiers__old;
                DROP TABLE wallet_tiers__old;
                CREATE INDEX IF NOT EXISTS idx_wallet_tiers_tier ON wallet_tiers(tier);
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )
            await self.conn.commit()
        # Always ensure the source_stage index — safe since source_stage
        # exists after migration or on fresh deploys.
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wallet_tiers_source_stage "
            "ON wallet_tiers(source_stage)"
        )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


_singleton: Database | None = None


async def get_db(path: Path | str | None = None) -> Database:
    """Return a process-wide Database singleton.

    First call must supply `path`. Subsequent calls can omit it.
    """
    global _singleton
    if _singleton is None:
        if path is None:
            raise RuntimeError("get_db first call requires a path")
        _singleton = Database(path)
        await _singleton.connect()
    return _singleton


async def reset_db_singleton() -> None:
    """Close and clear the singleton — used by tests."""
    global _singleton
    if _singleton is not None:
        await _singleton.close()
        _singleton = None
