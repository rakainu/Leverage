"""Audit-and-notify endpoint for scheduled remote agents.

This is the autonomous-audit path described in the 2026-04-28 redeploy: a
scheduled cloud agent (no SSH access, no Telegram credentials) calls this
endpoint with a Bearer token + a frozen baseline payload, the server runs
the audit SQL on the live DB, builds a comparison summary, posts the result
to Telegram via the project's existing bot, and returns a status JSON.

All secrets stay server-side. The cloud agent only carries the Bearer token.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from config.settings import Settings

logger = logging.getLogger("smc.dashboard.audit")


# ---- request/response schemas ------------------------------------------------


class BaselineCounts(BaseModel):
    n: int
    wins: int
    net_sol: float
    avg_pnl_pct: float
    wr_pct: float


class AuditRequest(BaseModel):
    since_utc: str = Field(..., description="ISO timestamp; only positions opened at-or-after are evaluated")
    telegram_chat_id: str | None = Field(None, description="Override default chat")
    baseline_label: str = Field(..., description="Human label, e.g. 'Pre-redeploy 22-day audit'")
    baseline: BaselineCounts


# ---- audit slices ------------------------------------------------------------


async def _slice_headline(db, since_utc: str) -> dict:
    rows = await db.execute_fetchall(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                  ROUND(SUM(pnl_sol),3) AS net_sol,
                  ROUND(AVG(pnl_pct),2) AS avg_pnl_pct
           FROM positions
           WHERE mode='paper' AND status='closed' AND opened_at >= ?""",
        (since_utc,),
    )
    r = dict(rows[0]) if rows else {"n": 0, "wins": 0, "net_sol": 0.0, "avg_pnl_pct": 0.0}
    n = r.get("n") or 0
    wins = r.get("wins") or 0
    return {
        "n": n,
        "wins": wins,
        "net_sol": r.get("net_sol") or 0.0,
        "avg_pnl_pct": r.get("avg_pnl_pct") or 0.0,
        "wr_pct": round(100.0 * wins / n, 1) if n else 0.0,
    }


async def _slice_close_reasons(db, since_utc: str) -> list[dict]:
    rows = await db.execute_fetchall(
        """SELECT close_reason, COUNT(*) AS n,
                  ROUND(AVG(pnl_pct),2) AS avg_pnl,
                  ROUND(SUM(pnl_sol),3) AS sum_pnl
           FROM positions
           WHERE mode='paper' AND status='closed' AND opened_at >= ?
           GROUP BY close_reason ORDER BY n DESC""",
        (since_utc,),
    )
    return [dict(r) for r in rows]


async def _slice_speed_buckets(db, since_utc: str) -> list[dict]:
    rows = await db.execute_fetchall(
        """SELECT CASE
                    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<1 THEN 'a_lt1m'
                    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<5 THEN 'b_1to5m'
                    WHEN (julianday(cs.signal_at)-julianday(cs.first_buy_at))*1440<15 THEN 'c_5to15m'
                    ELSE 'd_15mplus'
                  END AS bucket,
                  COUNT(*) AS n,
                  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                  ROUND(AVG(p.pnl_pct),2) AS avg_pnl,
                  ROUND(SUM(p.pnl_sol),3) AS sum_pnl
           FROM positions p
           JOIN convergence_signals cs ON cs.id=p.signal_id
           WHERE p.mode='paper' AND p.status='closed' AND p.opened_at >= ?
           GROUP BY bucket ORDER BY bucket""",
        (since_utc,),
    )
    return [dict(r) for r in rows]


async def _slice_toxic_hours(db, since_utc: str) -> dict:
    """Count trades that fired in 11-18 UTC despite the block (gate-leak detector)."""
    rows = await db.execute_fetchall(
        """SELECT COUNT(*) AS n,
                  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS wins,
                  ROUND(SUM(p.pnl_sol),3) AS sum_pnl
           FROM positions p
           JOIN convergence_signals cs ON cs.id=p.signal_id
           WHERE p.mode='paper' AND p.status='closed' AND p.opened_at >= ?
             AND CAST(strftime('%H', cs.signal_at) AS INTEGER) BETWEEN 11 AND 18""",
        (since_utc,),
    )
    r = dict(rows[0]) if rows else {"n": 0, "wins": 0, "sum_pnl": 0.0}
    return {
        "n": r.get("n") or 0,
        "wins": r.get("wins") or 0,
        "sum_pnl": r.get("sum_pnl") or 0.0,
    }


async def _slice_source_presence(db, since_utc: str) -> list[dict]:
    rows = await db.execute_fetchall(
        """SELECT tw.source, COUNT(*) AS appearances,
                  SUM(CASE WHEN p.pnl_pct>0 THEN 1 ELSE 0 END) AS in_winners,
                  ROUND(AVG(p.pnl_pct),2) AS avg_pnl_when_present
           FROM positions p
           JOIN convergence_signals cs ON cs.id=p.signal_id
           JOIN buy_events be ON be.token_mint=p.token_mint
                              AND be.timestamp <= cs.signal_at
                              AND be.timestamp >= cs.first_buy_at
           JOIN tracked_wallets tw ON tw.address = be.wallet_address
           WHERE p.mode='paper' AND p.status='closed' AND p.opened_at >= ?
           GROUP BY tw.source ORDER BY appearances DESC""",
        (since_utc,),
    )
    return [dict(r) for r in rows]


async def _slice_stop_slippage(db, since_utc: str) -> dict:
    rows = await db.execute_fetchall(
        """SELECT COUNT(*) AS n,
                  ROUND(AVG(pnl_pct),2) AS avg_close_pnl,
                  ROUND(MIN(pnl_pct),2) AS worst,
                  ROUND(AVG(high_watermark_pct),2) AS avg_hwm
           FROM positions
           WHERE mode='paper' AND status='closed'
             AND close_reason='stop_loss' AND opened_at >= ?""",
        (since_utc,),
    )
    r = dict(rows[0]) if rows else {"n": 0, "avg_close_pnl": 0.0, "worst": 0.0, "avg_hwm": 0.0}
    return {k: (r.get(k) or 0.0 if k != "n" else r.get(k) or 0) for k in ("n", "avg_close_pnl", "worst", "avg_hwm")}


# ---- comparison + formatting -------------------------------------------------


def _arrow(post: float, pre: float, *, more_is_better: bool) -> str:
    if post == pre:
        return "→"
    better = post > pre if more_is_better else post < pre
    return "✅" if better else "❌"


def _format_message(label: str, since_utc: str, post: dict, baseline: BaselineCounts) -> str:
    h = post["headline"]
    reasons = post["close_reasons"]
    speed = post["speed_buckets"]
    toxic = post["toxic_hours"]
    sources = post["source_presence"]
    stops = post["stop_slippage"]

    lines: list[str] = []
    lines.append("<b>📊 SMC POST-REDEPLOY AUDIT</b>")
    lines.append(f"<i>baseline: {label}</i>")
    lines.append(f"<i>since: {since_utc}</i>")
    lines.append("")

    if h["n"] == 0:
        lines.append("⚠️ <b>No closed positions in this window.</b>")
        lines.append("Either the signal/trade pipeline hasn't fired yet, or no trades have closed.")
        return "\n".join(lines)

    # Headline comparison
    lines.append("<b>Headline (post vs pre)</b>")
    lines.append(
        f"  trades   : {h['n']} (was {baseline.n})"
    )
    lines.append(
        f"  net SOL  : {h['net_sol']:+.3f} (was {baseline.net_sol:+.3f}) "
        f"{_arrow(h['net_sol'], baseline.net_sol, more_is_better=True)}"
    )
    lines.append(
        f"  avg pnl  : {h['avg_pnl_pct']:+.2f}% (was {baseline.avg_pnl_pct:+.2f}%) "
        f"{_arrow(h['avg_pnl_pct'], baseline.avg_pnl_pct, more_is_better=True)}"
    )
    lines.append(
        f"  win rate : {h['wr_pct']:.1f}% (was {baseline.wr_pct:.1f}%) "
        f"{_arrow(h['wr_pct'], baseline.wr_pct, more_is_better=True)}"
    )
    lines.append("")

    # Close reasons (post-redeploy only)
    lines.append("<b>Close reasons</b>")
    for r in reasons:
        lines.append(f"  {r.get('close_reason') or '(none)'}: n={r['n']}  avg={r['avg_pnl']:+.1f}%  sum={r['sum_pnl']:+.3f}")
    lines.append("")

    # Speed buckets — should be dominated by 5-15min now
    lines.append("<b>Convergence speed (gate set 5-15min)</b>")
    bucket_labels = {"a_lt1m": "&lt;1min ", "b_1to5m": "1-5min", "c_5to15m": "5-15m ", "d_15mplus": "15+m  "}
    for r in speed:
        bl = bucket_labels.get(r["bucket"], r["bucket"])
        flag = ""
        if r["bucket"] in ("a_lt1m", "b_1to5m", "d_15mplus") and r["n"] > 0:
            flag = "  ⚠️ outside 5-15min gate"
        lines.append(f"  {bl}: n={r['n']}  avg={r['avg_pnl']:+.1f}%  sum={r['sum_pnl']:+.3f}{flag}")
    lines.append("")

    # Toxic hours — should be 0
    if toxic["n"] == 0:
        lines.append("<b>Hours 11-18 UTC</b>: 0 trades ✅ (block working)")
    else:
        lines.append(
            f"<b>Hours 11-18 UTC</b>: {toxic['n']} trades, {toxic['wins']} wins, "
            f"{toxic['sum_pnl']:+.3f} SOL ⚠️ (block leaking)"
        )
    lines.append("")

    # Source presence — Nansen should be 0
    nansen_present = next((s for s in sources if s["source"] == "nansen-live"), None)
    if nansen_present:
        lines.append(
            f"⚠️ Nansen wallet appearances: {nansen_present['appearances']} (expected 0 — kill leaked)"
        )
    else:
        lines.append("Nansen presence: 0 ✅")
    for s in sources:
        if s["source"] == "nansen-live":
            continue
        lines.append(
            f"  {s['source']}: {s['appearances']} appear, {s['in_winners']} in winners, "
            f"avg {s['avg_pnl_when_present']:+.1f}%"
        )
    lines.append("")

    # Stop slippage
    if stops["n"] > 0:
        lines.append(
            f"<b>Stops (n={stops['n']})</b>: avg close {stops['avg_close_pnl']:+.1f}% "
            f"(SL configured at -25%; gap {stops['avg_close_pnl'] + 25:+.1f}%)"
        )
        lines.append(f"  avg HWM = {stops['avg_hwm']:+.2f}%  (>0 means trades did tick green)")
    else:
        lines.append("<b>Stops</b>: 0 — interesting, no stop-outs in this window")

    return "\n".join(lines)


async def _send_telegram(bot_token: str, chat_id: str, text: str) -> dict:
    if not bot_token:
        raise HTTPException(500, "telegram_bot_token not configured server-side")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        })
    if resp.status_code != 200:
        logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
        raise HTTPException(502, f"telegram api failed: {resp.status_code}")
    return resp.json()


# ---- registration ------------------------------------------------------------


def register_audit_routes(app: FastAPI, db, settings: Settings) -> None:
    @app.post("/api/audit-and-notify")
    async def audit_and_notify(
        payload: AuditRequest,
        authorization: str | None = Header(None),
    ) -> dict[str, Any]:
        if not settings.audit_token:
            raise HTTPException(503, "audit_token not configured server-side")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "missing bearer token")
        token = authorization[len("Bearer "):].strip()
        if token != settings.audit_token:
            raise HTTPException(401, "invalid bearer token")

        chat_id = payload.telegram_chat_id or settings.telegram_chat_id

        slices = {
            "headline": await _slice_headline(db, payload.since_utc),
            "close_reasons": await _slice_close_reasons(db, payload.since_utc),
            "speed_buckets": await _slice_speed_buckets(db, payload.since_utc),
            "toxic_hours": await _slice_toxic_hours(db, payload.since_utc),
            "source_presence": await _slice_source_presence(db, payload.since_utc),
            "stop_slippage": await _slice_stop_slippage(db, payload.since_utc),
        }

        msg = _format_message(payload.baseline_label, payload.since_utc, slices, payload.baseline)
        tg_resp = await _send_telegram(settings.telegram_bot_token, chat_id, msg)

        return {
            "status": "ok",
            "since_utc": payload.since_utc,
            "trades_found": slices["headline"]["n"],
            "telegram_sent": True,
            "telegram_message_id": tg_resp.get("result", {}).get("message_id"),
            "slices": slices,
        }
