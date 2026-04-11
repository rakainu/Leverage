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
