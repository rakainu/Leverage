"""FastAPI dashboard backend.

JSON endpoints:
- GET /api/health      — liveness + armed/paused state
- GET /api/stats       — top-bar counters
- GET /api/convergence — last-N signals + status
- GET /api/positions   — open paper positions
- GET /api/wallets     — top-N ranked wallets
- GET /api/coin/{sym}  — per-coin drill-in

Serves a single-page HTML at / that polls these endpoints every 5 seconds.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from hlsm.db import (
    PaperPosition,
    ScoreHistory,
    Signal,
    Wallet,
)
from hlsm.db.session import get_session
from hlsm.safety.circuit_breaker import CircuitBreaker
from hlsm.safety.state import get_safety_state

log = logging.getLogger(__name__)


def _serialize_state(sess: Session) -> dict:
    state = get_safety_state(sess)
    armed = (not state.paused_global) and (not state.drain_mode) and (not state.breaker_tripped)
    return {
        "armed": armed,
        "paused_global": state.paused_global,
        "drain_mode": state.drain_mode,
        "breaker_tripped": state.breaker_tripped,
        "paused_coins": sorted(state.paused_coins),
    }


def _day_pnl(sess: Session) -> Decimal:
    return CircuitBreaker(threshold_usdt=Decimal("1000000")).day_pnl_usdt(sess)


def create_app() -> FastAPI:
    app = FastAPI(title="HLSM Bridge", version="0.1.0")
    static_dir = Path(__file__).resolve().parent / "static"

    @app.get("/api/health")
    def health() -> dict:
        with get_session() as sess:
            state = _serialize_state(sess)
        return {"status": "ok", **state}

    @app.get("/api/stats")
    def stats() -> dict:
        now = datetime.now(timezone.utc)
        since_24h = now - timedelta(hours=24)
        with get_session() as sess:
            tracked = sess.execute(select(func.count(Wallet.address)).where(Wallet.active.is_(True))).scalar() or 0
            scored = sess.execute(
                select(func.count(Wallet.address)).where(Wallet.active.is_(True), Wallet.current_score.is_not(None))
            ).scalar() or 0
            convergence_24h = sess.execute(
                select(func.count(Signal.id)).where(Signal.fired_at >= since_24h)
            ).scalar() or 0
            open_positions = sess.execute(
                select(func.count(PaperPosition.id)).where(PaperPosition.status == "open")
            ).scalar() or 0
            day_pnl = _day_pnl(sess)
            state = _serialize_state(sess)
        return {
            "tracked_wallets": tracked,
            "scored_wallets": scored,
            "convergence_events_24h": convergence_24h,
            "open_positions": open_positions,
            "day_pnl_usdt": float(day_pnl),
            **state,
        }

    @app.get("/api/convergence")
    def convergence(limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 100))
        with get_session() as sess:
            rows = sess.execute(
                select(Signal).order_by(desc(Signal.fired_at)).limit(limit)
            ).scalars().all()
            out = []
            for s in rows:
                pp = s.paper_position
                out.append({
                    "id": s.id,
                    "fired_at": s.fired_at.isoformat(),
                    "coin": s.coin,
                    "side": s.side,
                    "wallet_count": s.wallet_count,
                    "wallet_addresses": s.wallet_addresses.split(","),
                    "score_floor": float(s.score_floor_used),
                    "window_minutes": s.window_seconds // 60,
                    "status": s.status,
                    "reason": s.reason,
                    "position": _position_to_dict(pp) if pp is not None else None,
                })
        return {"events": out}

    @app.get("/api/positions")
    def positions(status: str = "open", limit: int = 50) -> dict:
        with get_session() as sess:
            stmt = select(PaperPosition).order_by(desc(PaperPosition.opened_at)).limit(limit)
            if status != "all":
                stmt = select(PaperPosition).where(PaperPosition.status == status).order_by(desc(PaperPosition.opened_at)).limit(limit)
            rows = sess.execute(stmt).scalars().all()
            out = [_position_to_dict(pp) for pp in rows]
        return {"positions": out}

    @app.get("/api/wallets")
    def wallets(limit: int = 50) -> dict:
        with get_session() as sess:
            rows = sess.execute(
                select(Wallet)
                .where(Wallet.active.is_(True), Wallet.current_score.is_not(None))
                .order_by(desc(Wallet.current_score))
                .limit(limit)
            ).scalars().all()
            out = [{
                "address": w.address,
                "score": float(w.current_score) if w.current_score is not None else None,
                "trade_count": w.trade_count,
                "style": w.style,
                "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None,
            } for w in rows]
        return {"wallets": out}

    @app.get("/api/coin/{symbol}")
    def coin_detail(symbol: str) -> dict:
        symbol = symbol.upper()
        with get_session() as sess:
            signals = sess.execute(
                select(Signal).where(Signal.coin == symbol).order_by(desc(Signal.fired_at)).limit(10)
            ).scalars().all()
            positions = sess.execute(
                select(PaperPosition).where(PaperPosition.coin == symbol).order_by(desc(PaperPosition.opened_at)).limit(20)
            ).scalars().all()
            out_signals = [{
                "id": s.id,
                "fired_at": s.fired_at.isoformat(),
                "side": s.side,
                "wallet_count": s.wallet_count,
                "status": s.status,
            } for s in signals]
            out_positions = [_position_to_dict(pp) for pp in positions]
        return {"symbol": symbol, "signals": out_signals, "positions": out_positions}

    @app.get("/")
    def index() -> FileResponse:
        idx = static_dir / "index.html"
        if not idx.exists():
            raise HTTPException(status_code=500, detail="dashboard index.html not found")
        return FileResponse(idx)

    return app


def _position_to_dict(pp: PaperPosition) -> dict:
    return {
        "id": pp.id,
        "signal_id": pp.signal_id,
        "venue": pp.venue,
        "coin": pp.coin,
        "side": pp.side,
        "margin_usdt": float(pp.margin_usdt),
        "leverage": pp.leverage,
        "notional_usdt": float(pp.notional_usdt),
        "entry_px": float(pp.entry_px),
        "sl_px": float(pp.sl_px),
        "tp_px": float(pp.tp_px),
        "opened_at": pp.opened_at.isoformat(),
        "closed_at": pp.closed_at.isoformat() if pp.closed_at else None,
        "exit_px": float(pp.exit_px) if pp.exit_px is not None else None,
        "realized_pnl_usdt": float(pp.realized_pnl_usdt) if pp.realized_pnl_usdt is not None else None,
        "realized_pnl_pct": float(pp.realized_pnl_pct) if pp.realized_pnl_pct is not None else None,
        "status": pp.status,
        "close_reason": pp.close_reason,
    }
