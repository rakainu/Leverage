"""Async SQLite database layer for SMC."""

import aiosqlite
from pathlib import Path

_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Get or create the singleton database connection."""
    global _connection
    if _connection is None:
        from config.settings import Settings
        settings = Settings()
        db_path = Path(settings.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _connection = await aiosqlite.connect(str(db_path))
        _connection.row_factory = aiosqlite.Row
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA busy_timeout=5000")
    return _connection


async def init_db():
    """Initialize database with schema."""
    db = await get_db()
    schema_path = Path(__file__).parent / "schema.sql"
    await db.executescript(schema_path.read_text())
    await db.commit()


async def close_db():
    """Close the database connection."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
