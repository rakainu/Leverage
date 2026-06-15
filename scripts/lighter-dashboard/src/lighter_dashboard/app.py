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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# All wall-clock times in the UI are shown in Rich's local Pacific time.
# America/Los_Angeles auto-tracks DST, so this stays correct (PST in winter,
# PDT in summer) without any config. DB timestamps remain UTC ISO strings.
_LOCAL_TZ = ZoneInfo("America/Los_Angeles")

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

# Selectable realized-PnL windows -> lookback in days (None = all-time).
_REALIZED_WINDOWS = {"day": 1, "week": 7, "month": 30, "all": None}
# Human-readable period label shown on the Realized card.
_WINDOW_LABELS = {"day": "24h", "week": "7d", "month": "30d", "all": "all-time"}
_SIGNAL_LOOKBACK_HOURS = 12
# Per-coin scoreboard: below this trade count, a coin's verdict is "new" (too
# small a sample to keep/cut). PF thresholds mirror the strategy kill-switch.
_SCOREBOARD_MIN_SAMPLE = 10


def _keep_cut(n: int, pf, cushion) -> str:
    """At-a-glance keep/cut verdict for a coin. 'new' until enough trades, then
    by profit factor (kill-switch thresholds): keep >=1.15, cut <=0.95, else watch."""
    if n < _SCOREBOARD_MIN_SAMPLE:
        return "new"
    if pf is None or pf >= 1.15:
        return "keep"
    if pf <= 0.95:
        return "cut"
    return "watch"


def _short_age(secs: float) -> str:
    """'45s ago' / '3m ago' / '1h 4m ago' for a heartbeat age."""
    if secs < 60:
        return f"{int(secs)}s ago"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    hrs = mins / 60
    return f"{int(hrs)}h {int(mins % 60)}m ago"


def _heartbeat_status(iso: str | None) -> dict:
    """Live/stale status from the bridge's last heartbeat. Fresh = a beat within
    7 min (the bridge writes one every ~5 min, so this tolerates one miss)."""
    if not iso:
        return {"state": "starting", "ago": "", "ts": ""}
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return {"state": "starting", "ago": "", "ts": iso}
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    return {
        "state": "live" if secs < 420 else "stale",
        "ago": _short_age(secs),
        "ts": iso,
    }


def _tz_label() -> str:
    """Live Pacific abbreviation ('PST' / 'PDT') for column headers."""
    return datetime.now(_LOCAL_TZ).strftime("%Z")


def _fmt_close(iso: str | None) -> str:
    """Clock time a trade closed, as 'MM-DD HH:MM' in local Pacific time, so a
    stall is obvious at a glance (compare the newest close time to now).
    Returns '—' if missing."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ).strftime("%m-%d %H:%M")


def _fmt_hm(iso: str | None) -> str:
    """Just the 'HH:MM' clock time in local Pacific, for the signal log."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ).strftime("%H:%M")


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
    async def panel_kpis(request: Request, window: str = "all"):
        if window not in _REALIZED_WINDOWS:
            window = "all"
        pnls = db.closed_pnls()                       # all-time: equity, PF, drawdown
        positions = await _open_positions_with_pnl()
        realized_all = sum(pnls)
        unrealized = sum(p["upnl"] or 0 for p in positions)
        withdrawn = db.withdrawn_total()
        # gross = trading value (drives curve + true total return); equity = net balance
        # actually in the account after profit withdrawals.
        gross = cfg.initial_collateral_usdc + realized_all + unrealized
        equity = gross - withdrawn
        snaps = [s["portfolio_value"] for s in db.snapshots()] + [gross]
        days = _REALIZED_WINDOWS[window]
        if days is None:                              # all-time: no lower bound
            cutoff = "1970-01-01T00:00:00+00:00"
        else:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=days)).isoformat()
        win_net, win_n, win_wins = db.realized_since(cutoff)
        ctx = {
            "equity": equity,
            "equity_pct": (gross / cfg.initial_collateral_usdc - 1) * 100,
            "withdrawn": withdrawn,
            "n_open": len(positions),
            "realized": win_net,
            "realized_n": win_n,
            "realized_win_pct": (win_wins / win_n * 100) if win_n else 0,
            "window": window,
            "period_label": _WINDOW_LABELS[window],
            "profit_factor": stats.profit_factor(pnls),
            "max_dd": stats.max_drawdown(snaps),
            "poll_s": cfg.live_ms // 1000,
        }
        return templates.TemplateResponse(request, "partials/kpis.html", ctx)

    @app.get("/panel/withdrawals", response_class=HTMLResponse)
    async def panel_withdrawals(request: Request):
        rows = db.withdrawals(limit=52)
        total = db.withdrawn_total()
        for w in rows:
            w["when"] = _fmt_close(w.get("ts"))
        last = rows[0] if rows else None
        # account is held at the last withdrawal's equity_after (the target level)
        target = last["equity_after"] if last else None
        return templates.TemplateResponse(
            request, "partials/withdrawals.html",
            {"rows": rows, "total": total, "count": len(rows),
             "last": last, "target": target, "tz_label": _tz_label()},
        )

    @app.get("/panel/fillquality", response_class=HTMLResponse)
    async def panel_fillquality(request: Request):
        fq = db.fill_quality(limit=15)
        for r in fq["recent"]:
            r["when"] = _fmt_close(r.get("ts"))
        pnls = db.closed_pnls()
        live_wr = (sum(1 for p in pnls if p > 0) / len(pnls) * 100.0) if pnls else None
        return templates.TemplateResponse(
            request, "partials/fillquality.html",
            {"fq": fq, "live_wr": live_wr, "n_closed": len(pnls), "bt_wr": 88.0,
             "tz_label": _tz_label()},
        )

    @app.get("/panel/positions", response_class=HTMLResponse)
    async def panel_positions(request: Request):
        return templates.TemplateResponse(
            request, "partials/positions.html",
            {"positions": await _open_positions_with_pnl()},
        )

    @app.get("/panel/closed", response_class=HTMLResponse)
    async def panel_closed(request: Request):
        trades = db.closed_trades(limit=20)
        for t in trades:
            t["closed_hm"] = _fmt_close(t.get("closed_at"))
        return templates.TemplateResponse(
            request, "partials/closed_trades.html",
            {"trades": trades, "tz_label": _tz_label()},
        )

    @app.get("/panel/status", response_class=HTMLResponse)
    async def panel_status(request: Request):
        return templates.TemplateResponse(
            request, "partials/status.html",
            {"hb": _heartbeat_status(db.last_heartbeat_ts())},
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
            win_pct = (wins / n * 100) if n else 0
            gl = r.get("gross_loss") or 0
            pf = (r["gross_win"] / gl) if gl > 0 else None
            be = stats.breakeven_win_rate(r.get("avg_win") or 0,
                                          r.get("avg_loss") or 0)
            be_pct = be * 100 if be is not None else None
            cushion = (win_pct - be_pct) if be_pct is not None else None
            rows.append({
                **r, "win_pct": win_pct, "pf": pf,
                "be_pct": be_pct, "cushion": cushion,
                "verdict": _keep_cut(n, pf, cushion),
            })
        return templates.TemplateResponse(
            request, "partials/per_symbol.html",
            {"rows": rows, "min_sample": _SCOREBOARD_MIN_SAMPLE},
        )

    @app.get("/panel/signals", response_class=HTMLResponse)
    async def panel_signals(request: Request):
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(hours=_SIGNAL_LOOKBACK_HOURS)).isoformat()
        signals = db.signals(limit=50, since_iso=cutoff)
        for s in signals:
            # show the real event time (when it was detected / cancelled), not the
            # candle the signal belongs to — so a 3-bar unfilled cancel reads ~45m
            # after its detection, as it actually happened.
            s["event_hm"] = _fmt_hm(s.get("detected_at"))
        return templates.TemplateResponse(
            request, "partials/signals.html",
            {"signals": signals, "tz_label": _tz_label()},
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
