"""SQLAlchemy engine + sessionmaker factory. Sync for simplicity; FastAPI endpoints use thread-pool."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from hlsm.config import get_settings


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if _engine is None or url is not None:
        target_url = url or get_settings().hlsm_pg_url
        _engine = create_engine(target_url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    sm = get_sessionmaker()
    sess = sm()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


def reset_session_factory() -> None:
    """Test-only helper to re-bind to a fresh engine (e.g., SQLite in-memory)."""
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
