"""FastAPI dashboard endpoint tests using the TestClient."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hlsm.dashboard import create_app
from hlsm.db import PaperPosition, Signal, Wallet
from hlsm.db.models import Base
import hlsm.db.session as session_mod


@pytest.fixture()
def shared_engine():
    """Single SQLite in-memory engine that all connections share, so FastAPI worker threads see the same tables."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(shared_engine):
    SessionLocal = sessionmaker(bind=shared_engine, expire_on_commit=False, future=True)
    session_mod._engine = shared_engine
    session_mod._SessionLocal = SessionLocal
    sess = SessionLocal()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()


@pytest.fixture()
def client(session):
    app = create_app()
    return TestClient(app)


def _seed(session):
    session.add_all([
        Wallet(address="0xaaa", current_score=Decimal("90"), trade_count=200, active=True, style="scalper"),
        Wallet(address="0xbbb", current_score=Decimal("85"), trade_count=180, active=True, style="swing"),
    ])
    sig = Signal(
        fired_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        coin="PEPE", side="long", wallet_count=3,
        wallet_addresses="0xaaa,0xbbb,0xccc",
        score_floor_used=Decimal("75"), window_seconds=2700, status="filled",
    )
    session.add(sig)
    session.flush()
    pp = PaperPosition(
        signal_id=sig.id, venue="blofin-mock",
        coin="PEPE", side="long",
        margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
        entry_px=Decimal("0.00001"), sl_px=Decimal("0.0000075"), tp_px=Decimal("0.000013"),
        status="open",
    )
    session.add(pp)
    session.flush()


def test_health(client, session):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "armed" in body


def test_stats_endpoint_renders_counters(client, session):
    _seed(session)
    session.commit()
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["tracked_wallets"] >= 2
    assert body["scored_wallets"] >= 2
    assert body["open_positions"] >= 1
    assert "day_pnl_usdt" in body


def test_convergence_feed(client, session):
    _seed(session)
    session.commit()
    r = client.get("/api/convergence?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["events"]) == 1
    ev = body["events"][0]
    assert ev["coin"] == "PEPE"
    assert ev["status"] == "filled"
    assert ev["position"] is not None
    assert ev["position"]["coin"] == "PEPE"


def test_positions_endpoint(client, session):
    _seed(session)
    session.commit()
    r = client.get("/api/positions?status=open")
    assert r.status_code == 200
    body = r.json()
    assert len(body["positions"]) == 1
    assert body["positions"][0]["coin"] == "PEPE"


def test_wallets_endpoint_ordered_by_score(client, session):
    _seed(session)
    session.commit()
    r = client.get("/api/wallets?limit=10")
    assert r.status_code == 200
    body = r.json()
    addrs = [w["address"] for w in body["wallets"]]
    assert addrs == ["0xaaa", "0xbbb"]


def test_coin_drill_in(client, session):
    _seed(session)
    session.commit()
    r = client.get("/api/coin/pepe")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "PEPE"
    assert len(body["signals"]) >= 1
    assert len(body["positions"]) >= 1
