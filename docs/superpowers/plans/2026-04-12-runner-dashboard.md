# Runner-Intel Dashboard (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a compact, data-dense dashboard that reads live runner pipeline data from runner.db via 4 FastAPI routes + 1 health endpoint, rendered by a static HTML/JS frontend with 15-second polling.

**Architecture:** `queries.py` handles all SQL + JSON extraction (the clean boundary). `app.py` is a thin FastAPI layer that calls queries and serves static files. `index.html` defines layout, `app.js` handles fetch/render/polling. Dashboard runs as a uvicorn task in the existing `main.py` process.

**Tech Stack:** FastAPI, uvicorn, aiosqlite (existing), Tailwind CDN, vanilla JavaScript

**Spec:** `docs/superpowers/specs/2026-04-12-runner-dashboard-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `meme-trading/runner/requirements.txt` | Add fastapi + uvicorn |
| Create | `meme-trading/runner/dashboard/__init__.py` | Package marker |
| Create | `meme-trading/runner/dashboard/queries.py` | All SQL + JSON extraction |
| Create | `meme-trading/runner/dashboard/app.py` | FastAPI routes + static serve |
| Create | `meme-trading/runner/dashboard/static/index.html` | Page structure + Tailwind layout |
| Create | `meme-trading/runner/dashboard/static/app.js` | Fetch, render, polling |
| Modify | `meme-trading/runner/main.py` | Wire dashboard uvicorn task |
| Modify | `meme-trading/docker-compose.runner.yml` | Add port 8421 |
| Create | `meme-trading/runner/tests/unit/test_dashboard_queries.py` | Query tests |
| Create | `meme-trading/runner/tests/unit/test_dashboard_app.py` | Route tests |

---

### Task 1: Dependencies + queries.py

**Files:**
- Modify: `meme-trading/runner/requirements.txt`
- Create: `meme-trading/runner/dashboard/__init__.py`
- Create: `meme-trading/runner/dashboard/queries.py`
- Create: `meme-trading/runner/tests/unit/test_dashboard_queries.py`

- [ ] **Step 1: Add fastapi + uvicorn to requirements.txt**

Append to `meme-trading/runner/requirements.txt` after `python-telegram-bot`:

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
```

- [ ] **Step 2: Write query tests**

Create `meme-trading/runner/tests/unit/test_dashboard_queries.py`:

```python
"""Dashboard query layer tests — real DB with seeded data."""
import asyncio
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
            "cluster_quality": {"score": 70, "weight": 0.15, "weighted": 10.5, "detail": {}},
            "entry_quality": {"score": 75, "weight": 0.15, "weighted": 11.25, "detail": {}},
            "holder_quality": {"score": low_score, "weight": 0.15, "weighted": low_score * 0.15, "detail": {}},
            "rug_risk": {"score": 77, "weight": 0.15, "weighted": 11.55, "detail": {"insider_capped": insider_capped}},
            "follow_through": {"score": 60, "weight": 0.15, "weighted": 9.0, "detail": {}},
            "narrative": {"score": 50, "weight": 0.05, "weighted": 2.5, "detail": {"placeholder": True}},
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
    })


async def _seed_db(db):
    """Insert test data into all relevant tables."""
    now = datetime.now(timezone.utc)
    # cluster_signals
    await db.conn.execute(
        """INSERT INTO cluster_signals (id, token_mint, wallet_count, wallets_json, tier_counts_json,
           first_buy_time, last_buy_time, convergence_seconds, mid_price_sol)
           VALUES (1, 'MINT1', 3, '["A1","A2","B1"]', '{"A":2,"B":1}', ?, ?, 840, 0.0005)""",
        (now.isoformat(), (now + timedelta(minutes=14)).isoformat()),
    )
    # runner_scores
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
    # paper_positions
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
    scores = result["scores"]
    # First row (most recent by id) should be MINT2 or MINT1
    mint1_row = next(s for s in scores if s["token_mint"] == "MINT1")
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
    mint1_row = next(s for s in scores if s["token_mint"] == "MINT1" for scores in [result["scores"]])
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest runner/tests/unit/test_dashboard_queries.py -v`

Expected: FAIL — module not found.

- [ ] **Step 4: Create dashboard package and queries.py**

Create `meme-trading/runner/dashboard/__init__.py` (empty).

Create `meme-trading/runner/dashboard/queries.py`:

```python
"""Dashboard query layer — all SQL + JSON extraction. Read-only."""
import json
from datetime import datetime, timezone

from runner.db.database import Database


def _short_token(mint: str) -> str:
    if len(mint) <= 10:
        return mint
    return f"{mint[:4]}...{mint[-4:]}"


def _extract_top_reason(explanation_json: str) -> str:
    """Highest weighted dimension, excluding narrative placeholder."""
    try:
        exp = json.loads(explanation_json) if isinstance(explanation_json, str) else explanation_json
        dims = exp.get("dimensions", {})
    except (json.JSONDecodeError, TypeError):
        return "N/A"
    candidates = []
    for name, info in dims.items():
        if info.get("detail", {}).get("placeholder"):
            continue
        candidates.append((name, info.get("score", 0), info.get("weight", 0), info.get("weighted", 0)))
    if not candidates:
        return "N/A"
    candidates.sort(key=lambda x: x[3], reverse=True)
    name, score, weight, weighted = candidates[0]
    label = name.replace("_", " ").title()
    return f"{label} {score:.0f} (x{weight:.2f} = {weighted:.1f})"


def _extract_top_caution(explanation_json: str) -> str:
    """First dimension < 40, or data_degraded, or insider cap, or 'None'."""
    try:
        exp = json.loads(explanation_json) if isinstance(explanation_json, str) else explanation_json
        dims = exp.get("dimensions", {})
    except (json.JSONDecodeError, TypeError):
        return "N/A"
    for name, info in dims.items():
        if info.get("score", 100) < 40:
            label = name.replace("_", " ").title()
            return f"{label} {info['score']:.0f} — below threshold"
    if exp.get("data_degraded"):
        missing = exp.get("missing_subscores", [])
        return f"Data degraded — missing {', '.join(missing)}"
    rug = dims.get("rug_risk", {}).get("detail", {})
    if rug.get("insider_capped"):
        return "Insider risk cap triggered"
    return "None"


def _extract_dimensions(explanation_json: str) -> dict:
    try:
        exp = json.loads(explanation_json) if isinstance(explanation_json, str) else explanation_json
        dims = exp.get("dimensions", {})
    except (json.JSONDecodeError, TypeError):
        return {}
    return {
        name: {"score": info.get("score", 0), "weight": info.get("weight", 0), "weighted": info.get("weighted", 0)}
        for name, info in dims.items()
    }


def _extract_top_reasons(explanation_json: str) -> list[dict]:
    try:
        exp = json.loads(explanation_json) if isinstance(explanation_json, str) else explanation_json
        dims = exp.get("dimensions", {})
    except (json.JSONDecodeError, TypeError):
        return []
    candidates = []
    for name, info in dims.items():
        if info.get("detail", {}).get("placeholder"):
            continue
        candidates.append({
            "name": name.replace("_", " ").title(),
            "score": info.get("score", 0),
            "weight": info.get("weight", 0),
            "weighted": info.get("weighted", 0),
        })
    candidates.sort(key=lambda x: x["weighted"], reverse=True)
    return candidates[:3]


def _extract_cautions(explanation_json: str) -> list[str]:
    try:
        exp = json.loads(explanation_json) if isinstance(explanation_json, str) else explanation_json
        dims = exp.get("dimensions", {})
    except (json.JSONDecodeError, TypeError):
        return ["N/A"]
    cautions = []
    for name, info in dims.items():
        if info.get("score", 100) < 40:
            label = name.replace("_", " ").title()
            cautions.append(f"{label} {info['score']:.0f} — below threshold")
    if exp.get("data_degraded"):
        missing = exp.get("missing_subscores", [])
        cautions.append(f"Data degraded — missing {', '.join(missing)}")
    rug = dims.get("rug_risk", {}).get("detail", {})
    if rug.get("insider_capped"):
        cautions.append("Insider risk cap triggered")
    return cautions if cautions else ["None"]


async def get_health(db: Database) -> dict:
    """Lightweight health check."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        assert db.conn is not None
        await db.conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False
        return {"ok": False, "time": now, "db_reachable": False, "row_counts": {}}

    counts = {}
    for table in ("runner_scores", "paper_positions", "cluster_signals"):
        async with db.conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
            counts[table] = (await cur.fetchone())[0]

    return {"ok": True, "time": now, "db_reachable": True, "row_counts": counts}


async def get_stats(db: Database) -> dict:
    assert db.conn is not None
    async with db.conn.execute("SELECT COUNT(*) FROM runner_scores") as cur:
        total = (await cur.fetchone())[0]

    by_verdict = {}
    async with db.conn.execute("SELECT verdict, COUNT(*) FROM runner_scores GROUP BY verdict") as cur:
        async for verdict, count in cur:
            by_verdict[verdict] = count

    async with db.conn.execute(
        "SELECT AVG(runner_score) FROM runner_scores WHERE verdict NOT IN ('ignore')"
    ) as cur:
        avg_eligible = (await cur.fetchone())[0]

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions WHERE status = 'open'") as cur:
        open_pos = (await cur.fetchone())[0]

    async with db.conn.execute("SELECT COUNT(*) FROM paper_positions WHERE status = 'closed'") as cur:
        closed_pos = (await cur.fetchone())[0]

    async with db.conn.execute(
        "SELECT AVG(pnl_24h_pct) FROM paper_positions WHERE status = 'closed' AND pnl_24h_pct IS NOT NULL"
    ) as cur:
        avg_pnl = (await cur.fetchone())[0]

    return {
        "total_scored": total,
        "by_verdict": by_verdict,
        "avg_score_eligible": round(avg_eligible, 1) if avg_eligible else None,
        "open_positions": open_pos,
        "closed_positions": closed_pos,
        "avg_pnl_closed": round(avg_pnl, 1) if avg_pnl else None,
    }


async def get_scores(db: Database, limit: int = 50) -> dict:
    assert db.conn is not None
    async with db.conn.execute(
        """SELECT rs.id, rs.token_mint, rs.runner_score, rs.verdict, rs.short_circuited,
                  rs.explanation_json, rs.created_at,
                  EXISTS(SELECT 1 FROM paper_positions pp WHERE pp.runner_score_id = rs.id) as has_pos
           FROM runner_scores rs
           ORDER BY rs.created_at DESC
           LIMIT ?""",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()

    scores = []
    for row in rows:
        scores.append({
            "id": row[0],
            "token_mint": row[1],
            "short_token": _short_token(row[1]),
            "runner_score": row[2],
            "verdict": row[3],
            "short_circuited": bool(row[4]),
            "top_reason": _extract_top_reason(row[5]),
            "top_caution": _extract_top_caution(row[5]),
            "has_position": bool(row[7]),
            "created_at": row[6],
        })
    return {"scores": scores}


async def get_positions(db: Database, limit: int = 50) -> dict:
    assert db.conn is not None
    async with db.conn.execute(
        """SELECT id, token_mint, symbol, verdict, runner_score,
                  entry_price_sol, entry_price_usd, amount_sol,
                  pnl_5m_pct, pnl_30m_pct, pnl_1h_pct, pnl_4h_pct, pnl_24h_pct,
                  max_favorable_pct, max_adverse_pct,
                  status, close_reason, signal_time, opened_at, closed_at
           FROM paper_positions
           ORDER BY opened_at DESC
           LIMIT ?""",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()

    positions = []
    for r in rows:
        mint = r[1]
        symbol = r[2]
        positions.append({
            "id": r[0],
            "token_mint": mint,
            "short_token": _short_token(mint),
            "symbol": symbol if symbol else _short_token(mint),
            "verdict": r[3],
            "runner_score": r[4],
            "entry_price_sol": r[5],
            "entry_price_usd": r[6],
            "amount_sol": r[7],
            "pnl_5m": r[8], "pnl_30m": r[9], "pnl_1h": r[10], "pnl_4h": r[11], "pnl_24h": r[12],
            "mfe": r[13], "mae": r[14],
            "status": r[15], "close_reason": r[16],
            "signal_time": r[17], "opened_at": r[18], "closed_at": r[19],
        })
    return {"positions": positions}


async def get_score_detail(db: Database, score_id: int) -> dict | None:
    assert db.conn is not None
    async with db.conn.execute(
        """SELECT rs.id, rs.token_mint, rs.runner_score, rs.verdict, rs.short_circuited,
                  rs.sub_scores_json, rs.explanation_json, rs.created_at, rs.cluster_signal_id
           FROM runner_scores rs WHERE rs.id = ?""",
        (score_id,),
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        return None

    mint = row[1]
    explanation_json = row[6]
    sub_scores = {}
    try:
        sub_scores = json.loads(row[5]) if row[5] else {}
    except (json.JSONDecodeError, TypeError):
        pass

    explanation = {}
    try:
        explanation = json.loads(explanation_json) if explanation_json else {}
    except (json.JSONDecodeError, TypeError):
        pass

    # Cluster info from explanation
    cluster_dims = explanation.get("dimensions", {}).get("cluster_quality", {}).get("detail", {})
    wallet_dims = explanation.get("dimensions", {}).get("wallet_quality", {}).get("detail", {})

    # Position join
    position = None
    async with db.conn.execute(
        """SELECT id, entry_price_sol, entry_price_usd, amount_sol,
                  pnl_5m_pct, pnl_30m_pct, pnl_1h_pct, pnl_4h_pct, pnl_24h_pct,
                  max_favorable_pct, max_adverse_pct, status
           FROM paper_positions WHERE runner_score_id = ?""",
        (score_id,),
    ) as cur:
        pos_row = await cur.fetchone()

    if pos_row:
        position = {
            "id": pos_row[0], "entry_price_usd": pos_row[2], "amount_sol": pos_row[3],
            "pnl_5m": pos_row[4], "pnl_30m": pos_row[5], "pnl_1h": pos_row[6],
            "pnl_4h": pos_row[7], "pnl_24h": pos_row[8],
            "mfe": pos_row[9], "mae": pos_row[10], "status": pos_row[11],
        }

    return {
        "id": row[0],
        "token_mint": mint,
        "short_token": _short_token(mint),
        "runner_score": row[2],
        "verdict": row[3],
        "short_circuited": bool(row[4]),
        "created_at": row[7],
        "dimensions": _extract_dimensions(explanation_json),
        "top_reasons": _extract_top_reasons(explanation_json),
        "cautions": _extract_cautions(explanation_json),
        "raw_rug_risk": sub_scores.get("raw_rug_risk"),
        "raw_insider_risk": sub_scores.get("raw_insider_risk"),
        "cluster": {
            "wallet_count": cluster_dims.get("wallet_count") or wallet_dims.get("wallets", 0),
            "tier_counts": wallet_dims.get("tiers", []),
            "convergence_minutes": cluster_dims.get("convergence_minutes", 0),
        },
        "position": position,
        "scoring_version": explanation.get("scoring_version", "unknown"),
        "weights_hash": explanation.get("weights_hash", "unknown"),
        "links": {
            "dexscreener": f"https://dexscreener.com/solana/{mint}",
            "solscan": f"https://solscan.io/token/{mint}",
        },
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest runner/tests/unit/test_dashboard_queries.py -v`

Expected: All 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add runner/requirements.txt runner/dashboard/__init__.py runner/dashboard/queries.py runner/tests/unit/test_dashboard_queries.py
git commit -m "runner: dashboard queries layer with server-side JSON extraction"
```

---

### Task 2: FastAPI app + route tests

**Files:**
- Create: `meme-trading/runner/dashboard/app.py`
- Create: `meme-trading/runner/tests/unit/test_dashboard_app.py`

- [ ] **Step 1: Write route tests**

Create `meme-trading/runner/tests/unit/test_dashboard_app.py`:

```python
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
```

- [ ] **Step 2: Create app.py**

Create `meme-trading/runner/dashboard/app.py`:

```python
"""FastAPI dashboard — read-only API routes + static file serve."""
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from runner.dashboard.queries import (
    get_health,
    get_positions,
    get_score_detail,
    get_scores,
    get_stats,
)
from runner.db.database import Database

_STATIC = Path(__file__).parent / "static"


def create_app(db: Database) -> FastAPI:
    app = FastAPI(title="Runner Intel Dashboard", docs_url=None, redoc_url=None)

    @app.get("/api/health")
    async def health():
        return await get_health(db)

    @app.get("/api/stats")
    async def stats():
        return await get_stats(db)

    @app.get("/api/scores")
    async def scores(limit: int = Query(50, ge=1, le=200)):
        return await get_scores(db, limit=limit)

    @app.get("/api/scores/{score_id}")
    async def score_detail(score_id: int):
        result = await get_score_detail(db, score_id)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "score not found"})
        return result

    @app.get("/api/positions")
    async def positions(limit: int = Query(50, ge=1, le=200)):
        return await get_positions(db, limit=limit)

    @app.get("/")
    async def index():
        return FileResponse(_STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    return app
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest runner/tests/unit/test_dashboard_app.py -v`

Expected: All 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add runner/dashboard/app.py runner/tests/unit/test_dashboard_app.py
git commit -m "runner: FastAPI dashboard routes with health, stats, scores, positions"
```

---

### Task 3: Frontend — index.html + app.js

**Files:**
- Create: `meme-trading/runner/dashboard/static/index.html`
- Create: `meme-trading/runner/dashboard/static/app.js`

This is the largest task. The frontend is a thin renderer — all data arrives pre-processed from the API.

- [ ] **Step 1: Create index.html**

Create `meme-trading/runner/dashboard/static/index.html`. This is the page structure with Tailwind CDN. The subagent implementing this task should use the `frontend-design` skill to build a dark, compact, data-dense dashboard with:

**Layout:**
- Dark background (slate-900 / #0f172a)
- Full-width single column
- Section A: 6 summary cards in a row
- Section B: Scored candidates table with clickable rows
- Section C: Paper positions table
- Section D: Inline detail panel (hidden by default, shown on row click)

**Verdict badge colors:**
- ignore: gray-600
- watch: yellow-500
- strong_candidate: green-500
- probable_runner: blue-500

**Typography:** monospace for numbers/addresses, sans-serif for labels

**index.html must:**
- Load Tailwind CDN via `<script src="https://cdn.tailwindcss.com"></script>`
- Load `app.js` via `<script src="/static/app.js"></script>`
- Define all section containers with IDs: `#stats-cards`, `#scores-table`, `#positions-table`, `#detail-panel`
- Include a page header: "Runner Intel Dashboard" with a small status indicator

- [ ] **Step 2: Create app.js**

Create `meme-trading/runner/dashboard/static/app.js`. Pure vanilla JS:

**Functions needed:**
- `fetchJSON(url)` — async wrapper with error handling
- `refreshAll()` — fetches /api/stats, /api/scores, /api/positions in parallel, calls render functions
- `renderStats(data)` — builds 6 summary cards into `#stats-cards`
- `renderScores(scores)` — builds table rows into `#scores-table`, each row clickable
- `renderPositions(positions)` — builds table rows into `#positions-table`
- `showDetail(scoreId)` — fetches `/api/scores/{id}`, renders detail panel into `#detail-panel`
- `formatPnl(val)` — returns "+X.X%" green or "-X.X%" red or "—" for null
- `verdictBadge(verdict)` — returns HTML span with color class
- `formatTime(iso)` — short datetime format

**Polling:** `setInterval(refreshAll, 15000)` + initial `refreshAll()` on load.

**Detail panel content (shown on score row click):**
- 7 dimension bars (CSS width from score / 100)
- Top 3 reasons list
- Cautions list
- Raw rug + insider scores
- Cluster info
- Milestones (if position exists)
- DexScreener + Solscan links

- [ ] **Step 3: Test manually**

Run: `cd meme-trading && python -c "
import asyncio
from runner.db.database import Database
from runner.dashboard.app import create_app
import uvicorn

async def main():
    db = Database('runner/data/test_dashboard.db')
    await db.connect()
    app = create_app(db)
    config = uvicorn.Config(app, host='127.0.0.1', port=8421, log_level='info')
    server = uvicorn.Server(config)
    await server.serve()

asyncio.run(main())
"`

Open `http://localhost:8421` in browser. Verify layout renders (empty data is fine).

- [ ] **Step 4: Commit**

```bash
git add runner/dashboard/static/index.html runner/dashboard/static/app.js
git commit -m "runner: dashboard frontend — summary cards, scores table, positions table, detail panel"
```

---

### Task 4: Wire dashboard into main.py + Docker

**Files:**
- Modify: `meme-trading/runner/main.py`
- Modify: `meme-trading/docker-compose.runner.yml`

- [ ] **Step 1: Add dashboard task to main.py**

In `meme-trading/runner/main.py`, add import:

```python
from runner.dashboard.app import create_app
```

After the telegram alerter setup, add:

```python
    dashboard_app = create_app(db)
```

Add a dashboard runner function (inside `_main` or as a module-level helper):

```python
async def _run_dashboard(app, logger):
    """Run the dashboard as a uvicorn ASGI server."""
    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=8421, log_level="warning")
    server = uvicorn.Server(config)
    logger.info("dashboard_start", port=8421)
    await server.serve()
```

In `asyncio.gather(...)`, add:

```python
            _supervise(lambda: _run_dashboard(dashboard_app, logger), "dashboard", logger),
```

Update the task names in `zip(...)`.

- [ ] **Step 2: Add port to docker-compose.runner.yml**

In `meme-trading/docker-compose.runner.yml`, add under the `runner-intel` service:

```yaml
    ports:
      - "127.0.0.1:8421:8421"
```

- [ ] **Step 3: Verify import**

Run: `python -c "from runner.main import _main; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Run full test suite**

Run: `python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -15`

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add runner/main.py docker-compose.runner.yml
git commit -m "runner: wire dashboard into main.py + expose port 8421 in Docker"
```

---

### Task 5: Final push + verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest runner/tests/ -v 2>&1 | tail -5`

Expected: All tests pass (~215+).

- [ ] **Step 2: Push**

```bash
git push
```

---

## Summary

| Task | What it does | New tests |
|------|-------------|-----------|
| 1 | Dependencies + queries.py | 7 |
| 2 | FastAPI app.py + routes | 5 |
| 3 | Frontend HTML + JS | 0 (manual) |
| 4 | Wire into main.py + Docker | 0 |
| 5 | Final push | 0 |

**Total: 5 tasks, 12 new tests, ~5 commits**
