"""Dashboard query layer tests — real DB with seeded data."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from runner.db.database import Database


def _seed_explanation(top_score=87, low_score=38, insider_capped=False):
    return json.dumps({
        "scoring_version": "v1", "weights_mtime": 0, "weights_hash": "abc123",
        "short_circuited": False, "data_degraded": False, "missing_subscores": [],
        "failed_gate": None, "failed_reason": None,
        "dimensions": {
            "wallet_quality": {"score": top_score, "weight": 0.20, "weighted": top_score * 0.20, "detail": {}},
            "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5, "detail": {"wallet_count": 3, "convergence_minutes": 14.0}},
            "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25, "detail": {}},
            "holder_quality": {"score": low_score, "weight": 0.15, "weighted": low_score * 0.15, "detail": {}},
            "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55, "detail": {"insider_capped": insider_capped}},
            "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
            "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5, "detail": {"placeholder": True}},
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
    })


async def _seed_db(db):
    now = datetime.now(timezone.utc)
    await db.conn.execute(
        """INSERT INTO cluster_signals (id, token_mint, wallet_count, wallets_json, tier_counts_json,
           first_buy_time, last_buy_time, convergence_seconds, mid_price_sol)
           VALUES (1, 'MINT1', 3, '["A1","A2","B1"]', '{"A":2,"B":1}', ?, ?, 840, 0.0005)""",
        (now.isoformat(), (now + timedelta(minutes=14)).isoformat()),
    )
    sub = json.dumps({"wallet_quality": 87, "cluster_quality": 70, "entry_quality": 75,
                       "holder_quality": 38, "rug_risk": 77, "follow_through": 60, "narrative": 50,
                       "raw_rug_risk": 85, "raw_insider_risk": 50})
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id, runner_score, verdict,
           short_circuited, sub_scores_json, explanation_json)
           VALUES (1, 'MINT1', 1, 72.3, 'strong_candidate', 0, ?, ?)""",
        (sub, _seed_explanation()),
    )
    await db.conn.execute(
        """INSERT INTO runner_scores (id, token_mint, cluster_signal_id, runner_score, verdict,
           short_circuited, sub_scores_json, explanation_json)
           VALUES (2, 'MINT2', NULL, 15.0, 'ignore', 1, '{}', '{}')""",
    )
    await db.conn.execute(
        """INSERT INTO paper_positions (id, token_mint, symbol, runner_score_id, verdict, runner_score,
           entry_price_sol, entry_price_usd, amount_sol, signal_time, status, pnl_24h_pct,
           max_favorable_pct, max_adverse_pct)
           VALUES (1, 'MINT1', '$TEST', 1, 'strong_candidate', 72.3,
                   0.0006, 0.096, 0.25, ?, 'closed', 18.3, 52.1, -3.2)""",
        (now.isoformat(),),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_get_stats(tmp_path):
    from runner.dashboard.queries import get_stats
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    stats = await get_stats(db)
    assert stats["total_scored"] == 2
    assert stats["by_verdict"]["strong_candidate"] == 1
    assert stats["by_verdict"]["ignore"] == 1
    assert stats["closed_positions"] == 1
    assert stats["open_positions"] == 0
    assert stats["avg_pnl_closed"] == pytest.approx(18.3, abs=0.1)
    await db.close()


@pytest.mark.asyncio
async def test_get_scores_extracts_top_reason(tmp_path):
    from runner.dashboard.queries import get_scores
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    result = await get_scores(db, limit=50)
    mint1_row = next(s for s in result["scores"] if s["token_mint"] == "MINT1")
    assert "Wallet Quality" in mint1_row["top_reason"]
    assert mint1_row["has_position"] is True
    await db.close()


@pytest.mark.asyncio
async def test_get_scores_extracts_top_caution(tmp_path):
    from runner.dashboard.queries import get_scores
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    result = await get_scores(db, limit=50)
    mint1_row = next(s for s in result["scores"] if s["token_mint"] == "MINT1")
    assert "Holder Quality" in mint1_row["top_caution"]
    await db.close()


@pytest.mark.asyncio
async def test_get_positions(tmp_path):
    from runner.dashboard.queries import get_positions
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    result = await get_positions(db, limit=50)
    assert len(result["positions"]) == 1
    pos = result["positions"][0]
    assert pos["symbol"] == "$TEST"
    assert pos["pnl_24h"] == pytest.approx(18.3, abs=0.1)
    assert pos["mfe"] == pytest.approx(52.1, abs=0.1)
    assert pos["mae"] == pytest.approx(-3.2, abs=0.1)
    await db.close()


@pytest.mark.asyncio
async def test_get_score_detail(tmp_path):
    from runner.dashboard.queries import get_score_detail
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    detail = await get_score_detail(db, score_id=1)
    assert detail is not None
    assert detail["runner_score"] == pytest.approx(72.3, abs=0.1)
    assert len(detail["dimensions"]) == 7
    assert len(detail["top_reasons"]) == 3
    assert detail["raw_rug_risk"] == 85
    assert detail["raw_insider_risk"] == 50
    assert detail["position"] is not None
    assert detail["position"]["status"] == "closed"
    assert "dexscreener" in detail["links"]["dexscreener"]
    assert detail["scoring_version"] == "v1"
    await db.close()


@pytest.mark.asyncio
async def test_get_score_detail_no_position(tmp_path):
    from runner.dashboard.queries import get_score_detail
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    detail = await get_score_detail(db, score_id=2)
    assert detail is not None
    assert detail["position"] is None
    await db.close()


@pytest.mark.asyncio
async def test_get_health(tmp_path):
    from runner.dashboard.queries import get_health
    db = Database(tmp_path / "r.db")
    await db.connect()
    await _seed_db(db)
    health = await get_health(db)
    assert health["ok"] is True
    assert health["db_reachable"] is True
    assert health["row_counts"]["runner_scores"] == 2
    await db.close()
