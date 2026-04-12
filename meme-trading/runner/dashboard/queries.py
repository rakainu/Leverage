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
    now = datetime.now(timezone.utc).isoformat()
    try:
        assert db.conn is not None
        await db.conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        return {"ok": False, "time": now, "db_reachable": False, "row_counts": {}}

    counts = {}
    for table in ("runner_scores", "paper_positions", "cluster_signals"):
        async with db.conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:  # noqa: S608
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
                  rs.sub_scores_json, rs.explanation_json, rs.created_at
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

    cluster_detail = explanation.get("dimensions", {}).get("cluster_quality", {}).get("detail", {})
    wallet_detail = explanation.get("dimensions", {}).get("wallet_quality", {}).get("detail", {})

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
            "wallet_count": cluster_detail.get("wallet_count") or wallet_detail.get("wallets", 0),
            "tier_counts": wallet_detail.get("tiers", []),
            "convergence_minutes": cluster_detail.get("convergence_minutes", 0),
        },
        "position": position,
        "scoring_version": explanation.get("scoring_version", "unknown"),
        "weights_hash": explanation.get("weights_hash", "unknown"),
        "links": {
            "dexscreener": f"https://dexscreener.com/solana/{mint}",
            "solscan": f"https://solscan.io/token/{mint}",
        },
    }
