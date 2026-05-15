"""Test fixtures. Use SQLite in-memory + a fresh schema per test for fast isolation."""
from __future__ import annotations

import os
from decimal import Decimal
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ensure tests run with a deterministic config before any module imports settings
os.environ.setdefault("HLSM_PG_URL", "sqlite:///:memory:")
os.environ.setdefault("HLSM_CONFIG", os.path.join(os.path.dirname(__file__), "fixtures_weights.yaml"))
os.environ.setdefault("BLOFIN_API_KEY", "test-key")
os.environ.setdefault("BLOFIN_API_SECRET", "test-secret")
os.environ.setdefault("BLOFIN_API_PASSPHRASE", "test-pass")
os.environ.setdefault("BLOFIN_ENV", "demo")

# Reset the settings module cache so env vars take effect
from hlsm import config as cfg_mod  # noqa: E402
cfg_mod._settings = None

from hlsm.db.models import Base, Wallet  # noqa: E402
from hlsm.safety.state import reset_for_tests  # noqa: E402


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine) -> Session:
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess = SessionLocal()
    reset_for_tests()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


@pytest.fixture()
def seeded_wallets(session):
    """Three top-ranked wallets, scores 80/85/90, plus one low-score wallet at 60."""
    wallets = [
        Wallet(address="0xaaa", current_score=Decimal("90"), trade_count=200, source="seed"),
        Wallet(address="0xbbb", current_score=Decimal("85"), trade_count=180, source="seed"),
        Wallet(address="0xccc", current_score=Decimal("80"), trade_count=150, source="seed"),
        Wallet(address="0xddd", current_score=Decimal("60"), trade_count=80, source="seed"),
    ]
    session.add_all(wallets)
    session.commit()
    return wallets


@pytest.fixture()
def now_utc():
    return datetime.now(timezone.utc)
