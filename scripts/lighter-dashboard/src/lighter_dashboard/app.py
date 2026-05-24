"""FastAPI app for the Lighter dashboard.

Routes:
  GET /                -> full page shell (templates/index.html)
  GET /panel/kpis      -> KPI strip partial (live, 3s)
  GET /panel/positions -> open positions partial (live, 3s)
  GET /panel/equity    -> equity curve data + svg (static, 15s)
  GET /panel/closed    -> recent closed trades (static, 15s)
  GET /panel/exits     -> exit-reason mix (static, 15s)
  GET /panel/symbols   -> per-symbol stats (static, 15s)
  GET /panel/signals   -> signal log (static, 15s)

Basic-auth is handled by Traefik in front of this app, so there is no
auth code here.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import stats
from .config import DashboardConfig
from .db import DashboardDB
from .marks import MarkCache

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def create_app(cfg: DashboardConfig, marks=None) -> FastAPI:
    db = DashboardDB(cfg.db_path)
    mark_cache = marks if marks is not None else MarkCache(
        cfg.lighter_host, cfg.symbols, ttl=cfg.mark_cache_ttl_s
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await mark_cache.close()

    app = FastAPI(title="Lighter Dashboard", lifespan=lifespan)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    async def _open_positions_with_pnl() -> list[dict]:
        out = []
        for row in db.open_trades():
            mark = await mark_cache.get_mid(row["symbol"])
            upnl = None
            if mark is not None:
                upnl = stats.unrealized_pnl(
                    row["side"], row["entry_price"], mark, row["base_amount"]
                )
            out.append({**row, "mark": mark, "upnl": upnl})
        return out

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            request, "index.html", {"cfg": cfg}
        )

    @app.get("/panel/kpis", response_class=HTMLResponse)
    async def panel_kpis(request: Request):
        pnls = db.closed_pnls()
        positions = await _open_positions_with_pnl()
        realized = sum(pnls)
        unrealized = sum(p["upnl"] or 0 for p in positions)
        equity = cfg.initial_collateral_usdc + realized + unrealized
        snaps = [s["portfolio_value"] for s in db.snapshots()] + [equity]
        ctx = {
            "equity": equity,
            "equity_pct": (equity / cfg.initial_collateral_usdc - 1) * 100,
            "n_open": len(positions),
            "realized": realized,
            "n_closed": len(pnls),
            "win_rate": stats.win_rate(pnls) * 100,
            "profit_factor": stats.profit_factor(pnls),
            "max_dd": stats.max_drawdown(snaps),
        }
        return templates.TemplateResponse(request, "partials/kpis.html", ctx)

    @app.get("/panel/positions", response_class=HTMLResponse)
    async def panel_positions(request: Request):
        return templates.TemplateResponse(
            request, "partials/positions.html",
            {"positions": await _open_positions_with_pnl()},
        )

    @app.get("/panel/closed", response_class=HTMLResponse)
    async def panel_closed(request: Request):
        return templates.TemplateResponse(
            request, "partials/closed_trades.html",
            {"trades": db.closed_trades(limit=20)},
        )

    @app.get("/panel/exits", response_class=HTMLResponse)
    async def panel_exits(request: Request):
        return templates.TemplateResponse(
            request, "partials/exit_reasons.html",
            {"mix": db.exit_reason_mix()},
        )

    @app.get("/panel/symbols", response_class=HTMLResponse)
    async def panel_symbols(request: Request):
        rows = []
        for r in db.per_symbol_stats():
            n, wins = r["n"], (r["wins"] or 0)
            rows.append({**r, "win_pct": (wins / n * 100) if n else 0})
        return templates.TemplateResponse(
            request, "partials/per_symbol.html", {"rows": rows}
        )

    @app.get("/panel/signals", response_class=HTMLResponse)
    async def panel_signals(request: Request):
        return templates.TemplateResponse(
            request, "partials/signals.html",
            {"signals": db.signals(limit=30)},
        )

    @app.get("/panel/equity", response_class=HTMLResponse)
    async def panel_equity(request: Request):
        snaps = db.snapshots()
        values = [s["portfolio_value"] for s in snaps]
        points = _svg_points(values, width=600, height=200)
        return templates.TemplateResponse(
            request, "partials/equity.html",
            {"points": points, "has_data": len(values) > 1},
        )

    return app


def _svg_points(values: list[float], width: int, height: int) -> str:
    """Map a value series to an SVG polyline 'points' string."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i / (n - 1) * width
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)
