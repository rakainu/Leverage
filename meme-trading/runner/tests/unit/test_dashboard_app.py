"""Dashboard FastAPI route tests."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from runner.db.database import Database


async def _seed_db(db):
    now = datetime.now(timezone.utc)
    sub = json.dumps({"wallet_quality": 87, "cluster_quality": 70, "entry_quality": 75,
                       "holder_quality": 38, "rug_risk": 77, "follow_through": 60, "narrative": 50,
                       "raw_rug_risk": 85, "raw_insider_risk": 50})
    explanation = json.dumps({
        "scoring_version": "v1", "weights_mtime": 0, "weights_hash": "abc123",
        "short_circuited": False, "data_degraded": False, "missing_subscores": [],
        "failed_gate": None, "failed_reason": None,
        "dimensions": {
            "wallet_quality": {"score": 87, "weight": 0.20, "weighted": 17.4, "detail": {}},
            "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5, "detail": {}},
            "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25, "detail": {}},
            "holder_quality": {"score": 38, "weight": 0.15, "weighted": 5.7, "detail": {}},
            "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55, "detail": {"insider_capped": False}},
            "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
            "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5, "detail": {"placeholder": True}},
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
    })
    await db.conn.execute(
        """INSERT INTO cluster_signals (id, token_mint, wallet_count, wallets_json, tier_counts_json,
           first_buy_time, last_buy_time, convergence_seconds, mid_price_sol)
           VALUES (1, 'MINT1', 3, '["A1","A2","B1"]', '{"A":2,"B":1}', ?, ?, 840, 0.0005)""",
        (now.isoformat(), (now + timedelta(minutes=14)).isoformat()),
    )
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id, runner_score, verdict,
           short_circuited, sub_scores_json, explanation_json)
           VALUES (1, 'MINT1', 1, 72.3, 'strong_candidate', 0, ?, ?)""",
        (sub, explanation),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_health_endpoint(tmp_path):
    from runner.dashboard.app import create_app
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["db_reachable"] is True
    await db.close()


@pytest.mark.asyncio
async def test_stats_endpoint(tmp_path):
    from runner.dashboard.app import create_app
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_scored"] == 1
    await db.close()


@pytest.mark.asyncio
async def test_scores_endpoint(tmp_path):
    from runner.dashboard.app import create_app
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scores?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["scores"]) == 1
    assert data["scores"][0]["verdict"] == "strong_candidate"
    await db.close()


@pytest.mark.asyncio
async def test_score_detail_endpoint(tmp_path):
    from runner.dashboard.app import create_app
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scores/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["runner_score"] == pytest.approx(72.3, abs=0.1)
    assert "dimensions" in data
    assert data["scoring_version"] == "v1"
    await db.close()


@pytest.mark.asyncio
async def test_score_detail_404(tmp_path):
    from runner.dashboard.app import create_app
    db = Database(tmp_path / "r.db")
    await db.connect()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/scores/999")
    assert resp.status_code == 404
    await db.close()
