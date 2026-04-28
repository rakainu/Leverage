"""FastAPI application for the SMC trading dashboard."""

import csv
import io
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse

from config.settings import Settings
from dashboard.audit import register_audit_routes
from dashboard.websocket_manager import WebSocketManager

logger = logging.getLogger("smc.dashboard.app")


def create_app(ws_manager: WebSocketManager, db, settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI dashboard app."""
    app = FastAPI(title="SMC Trading Dashboard")
    if settings is None:
        settings = Settings()
    register_audit_routes(app, db, settings)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = Path(__file__).parent / "static" / "index.html"
        return HTMLResponse(html_path.read_text())

    @app.get("/api/signals")
    async def get_signals(limit: int = 50):
        rows = await db.execute_fetchall(
            "SELECT * FROM convergence_signals ORDER BY signal_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/positions")
    async def get_positions(status: str = "all", limit: int = 100):
        if status == "all":
            rows = await db.execute_fetchall(
                "SELECT * FROM positions ORDER BY opened_at DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM positions WHERE status=? ORDER BY opened_at DESC LIMIT ?",
                (status, limit),
            )
        return [dict(r) for r in rows]

    @app.get("/api/positions/open")
    async def get_open_positions():
        rows = await db.execute_fetchall(
            "SELECT * FROM positions WHERE status='open' ORDER BY opened_at DESC"
        )
        return [dict(r) for r in rows]

    @app.get("/api/wallets")
    async def get_wallets():
        rows = await db.execute_fetchall(
            "SELECT * FROM tracked_wallets WHERE active=1 ORDER BY score DESC"
        )
        return [dict(r) for r in rows]

    @app.get("/api/wallets/csv")
    async def get_wallets_csv():
        rows = await db.execute_fetchall(
            "SELECT address, label, source, score, total_trades, win_rate, "
            "total_pnl_sol, avg_hold_minutes, active, added_at, updated_at "
            "FROM tracked_wallets WHERE active=1 ORDER BY score DESC"
        )
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "address", "label", "source", "score", "total_trades",
            "win_rate", "total_pnl_sol", "avg_hold_minutes", "active",
            "added_at", "updated_at",
        ])
        for r in rows:
            writer.writerow([dict(r)[c] for c in [
                "address", "label", "source", "score", "total_trades",
                "win_rate", "total_pnl_sol", "avg_hold_minutes", "active",
                "added_at", "updated_at",
            ]])
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=tracked_wallets.csv"},
        )

    @app.get("/api/wallets/recent-count")
    async def get_recent_wallet_count():
        rows = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM tracked_wallets WHERE active=1 AND added_at >= datetime('now', '-6 hours')"
        )
        return {"added_last_6h": rows[0]["cnt"] if rows else 0}

    @app.get("/api/events")
    async def get_events(limit: int = 100):
        rows = await db.execute_fetchall(
            "SELECT * FROM buy_events ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    @app.get("/api/stats")
    async def get_stats():
        # Aggregate stats
        total_signals = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM convergence_signals"
        )
        total_trades = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM positions"
        )
        open_trades = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM positions WHERE status='open'"
        )
        closed_trades = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt, "
            "SUM(CASE WHEN pnl_sol > 0 THEN 1 ELSE 0 END) as wins, "
            "SUM(CASE WHEN pnl_sol <= 0 THEN 1 ELSE 0 END) as losses, "
            "SUM(pnl_sol) as total_pnl, "
            "AVG(pnl_pct) as avg_pnl_pct "
            "FROM positions WHERE status='closed'"
        )

        closed = dict(closed_trades[0]) if closed_trades else {}
        return {
            "total_signals": total_signals[0]["cnt"] if total_signals else 0,
            "total_trades": total_trades[0]["cnt"] if total_trades else 0,
            "open_trades": open_trades[0]["cnt"] if open_trades else 0,
            "wins": closed.get("wins") or 0,
            "losses": closed.get("losses") or 0,
            "total_pnl_sol": round(closed.get("total_pnl") or 0, 4),
            "avg_pnl_pct": round(closed.get("avg_pnl_pct") or 0, 2),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await ws_manager.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)

    return app
