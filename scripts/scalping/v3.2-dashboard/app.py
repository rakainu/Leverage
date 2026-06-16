"""V3.2 telemetry dashboard — live demo vs engine-predicted tracking.

Read-only FastAPI app over the scalping-v3.2 bridge SQLite DB. Serves one
bespoke page plus a /api/state JSON endpoint the page polls. Never writes.

Env:
  V32_DB    path to bridge.db (default ./data/bridge.db)
  V32_TITLE / V32_SUBTITLE  header text
"""
from __future__ import annotations

import os
import json
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import benchmark as B

DB_PATH = Path(os.environ.get("V32_DB", "data/bridge.db"))
TITLE = os.environ.get("V32_TITLE", "SCALPING V3.2")
SUBTITLE = os.environ.get("V32_SUBTITLE", "self-generated HA-V3 · BloFin demo")
STOP_USDT = float(os.environ.get("V32_STOP_USDT", "82.5"))  # %-stop in $ (all coins)
SYMBOLS = [s.strip() for s in os.environ.get(
    "V32_SYMBOLS",
    "ZEC-USDT,XRP-USDT,DOGE-USDT,SOL-USDT,BTC-USDT,BNB-USDT,HYPE-USDT",
).split(",") if s.strip()]

# Mark-price cache (public BloFin tickers) for open-position unrealized P&L.
_marks: dict = {"t": 0.0, "px": {}}


def _fetch_marks() -> dict:
    now = time.time()
    if now - _marks["t"] < 3.0 and _marks["px"]:
        return _marks["px"]
    try:
        req = urllib.request.Request(
            "https://openapi.blofin.com/api/v1/market/tickers",
            headers={"User-Agent": "Mozilla/5.0"},  # default UA is WAF-blocked (403)
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.load(r).get("data", [])
        _marks["px"] = {row["instId"]: float(row["last"])
                        for row in data if row.get("last")}
        _marks["t"] = now
    except Exception:
        pass
    return _marks["px"]

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app = FastAPI(title="Scalping V3.2 Telemetry")


# --------------------------------------------------------------------------
# Data access (read-only)
# --------------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only = ON;")
    return c


def _rows(sql: str, params=()) -> list[dict]:
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Stats (pure)
# --------------------------------------------------------------------------
def _kpis(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win_rate": None, "profit_factor": None, "avg_r": None,
                "avg_trade": None, "net": 0.0, "max_dd": 0.0, "max_consec_loss": 0,
                "long_n": 0, "short_n": 0, "long_net": 0.0, "short_net": 0.0,
                "avg_win": None, "avg_loss": None}
    pnls = [t["pnl_usdt"] or 0.0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), -sum(losses)
    net = sum(pnls)
    cum, peak, dd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    mc = c = 0
    for p in pnls:
        if p <= 0:
            c += 1; mc = max(mc, c)
        else:
            c = 0
    longs = [t["pnl_usdt"] or 0.0 for t in trades if t["side"] == "long"]
    shorts = [t["pnl_usdt"] or 0.0 for t in trades if t["side"] == "short"]
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades),
        # 999 = sentinel for "no losses yet" (∞); JSON can't carry float('inf').
        "profit_factor": (gw / gl) if gl > 0 else (999.0 if gw > 0 else 0.0),
        "avg_r": (net / len(trades)) / STOP_USDT if STOP_USDT else None,
        "avg_trade": net / len(trades),
        "net": net,
        "max_dd": dd,
        "max_consec_loss": mc,
        "long_n": len(longs), "short_n": len(shorts),
        "long_net": sum(longs), "short_net": sum(shorts),
        "avg_win": (gw / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
    }


def _exit_mix(trades: list[dict]) -> dict:
    if not trades:
        return {}
    out: dict[str, int] = {}
    for t in trades:
        r = t.get("exit_reason") or "open"
        out[r] = out.get(r, 0) + 1
    return {k: v / len(trades) for k, v in out.items()}


def _track(metric: str, live, higher_is_better=True) -> str:
    """Verdict for a metric vs the engine band: ahead / on_track / lagging / warmup."""
    band = B.ENGINE["band"].get(metric)
    if live is None or band is None:
        return "warmup"
    lo, hi = band
    if live >= hi:
        return "ahead"
    if live >= lo:
        return "on_track"
    return "lagging"


def _open_positions() -> list[dict]:
    rows = _rows(
        "SELECT id, symbol, side, entry_price, margin_usdt, leverage, "
        "trail_active, opened_at FROM positions WHERE closed_at IS NULL ORDER BY opened_at"
    )
    px = _fetch_marks() if rows else {}
    states = {0: "armed", 1: "breakeven", 2: "locked", 3: "locked+", 4: "trailing"}
    out = []
    for p in rows:
        cur = px.get(p["symbol"])
        notional = (p["margin_usdt"] or 0) * (p["leverage"] or 0)
        upnl = None
        if cur and p["entry_price"]:
            move = ((cur - p["entry_price"]) / p["entry_price"]) if p["side"] == "long" \
                else ((p["entry_price"] - cur) / p["entry_price"])
            upnl = move * notional
        out.append({
            "symbol": p["symbol"], "side": p["side"], "entry": p["entry_price"],
            "mark": cur, "upnl": upnl, "upnl_r": (upnl / STOP_USDT) if upnl is not None else None,
            "state": states.get(p["trail_active"], "—"), "opened_at": p["opened_at"],
        })
    return out


def _per_coin(trades: list[dict], open_syms: set) -> list[dict]:
    by: dict[str, list[float]] = {}
    for t in trades:
        by.setdefault(t["symbol"], []).append(t["pnl_usdt"] or 0.0)
    out = []
    for s in SYMBOLS:
        pn = by.get(s, [])
        wins = [x for x in pn if x > 0]
        gw, gl = sum(wins), -sum(x for x in pn if x <= 0)
        out.append({
            "symbol": s, "n": len(pn), "net": round(sum(pn), 1),
            "win_rate": (len(wins) / len(pn)) if pn else None,
            "profit_factor": (gw / gl) if gl > 0 else (999.0 if gw > 0 else None),
            "open": s in open_syms,
        })
    return sorted(out, key=lambda r: (r["open"], r["net"]), reverse=True)


def build_state() -> dict:
    trades = _rows(
        "SELECT id, symbol, side, entry_price, exit_price, exit_reason, "
        "pnl_usdt, pnl_pct, opened_at, closed_at, duration_secs "
        "FROM trade_log ORDER BY closed_at"
    )
    pending = _rows(
        "SELECT id, symbol, action, signal_price, created_at, status, fill_price "
        "FROM pending_signals ORDER BY id DESC LIMIT 40"
    )
    live = _kpis(trades)

    # running span
    first = _parse(trades[0]["opened_at"]) if trades else None
    if not first and pending:
        first = _parse(pending[-1]["created_at"])
    now = datetime.now(timezone.utc)
    days = ((now - first).total_seconds() / 86400.0) if first else 0.0
    live["days_running"] = days
    # Per-day rates are meaningless extrapolated from a sub-day window
    # (2 trades in an hour is NOT "49/day"). Suppress until ≥1 day elapsed.
    if days >= 1.0:
        live["trades_per_day"] = live["n"] / days
        live["net_per_day"] = live["net"] / days
    else:
        live["trades_per_day"] = None
        live["net_per_day"] = None

    # equity curve (cumulative net by close order)
    cum = 0.0
    curve = []
    for t in trades:
        cum += t["pnl_usdt"] or 0.0
        curve.append(round(cum, 2))

    n_for_track = live["n"]
    tracking_ready = n_for_track >= B.TRACKING_MIN_N
    tracking = {
        "ready": tracking_ready,
        "n": n_for_track,
        "min_n": B.TRACKING_MIN_N,
        "win_rate": _track("win_rate", live["win_rate"]) if tracking_ready else "warmup",
        "profit_factor": _track("profit_factor", live["profit_factor"]) if tracking_ready else "warmup",
        "avg_r": _track("avg_r", live["avg_r"]) if tracking_ready else "warmup",
        "trades_per_day": _track("trades_per_day", live["trades_per_day"]) if tracking_ready else "warmup",
    }

    pend_open = [p for p in pending if p["status"] == "pending"]
    open_pos = _open_positions()
    per_coin = _per_coin(trades, {o["symbol"] for o in open_pos})
    return {
        "open_positions": open_pos,
        "per_coin": per_coin,
        "meta": {
            "title": TITLE, "subtitle": SUBTITLE,
            "updated": now.strftime("%H:%M:%S UTC"),
            "days_running": round(days, 1),
            "stop_usdt": STOP_USDT,
        },
        "engine": B.ENGINE,
        "live": live,
        "tracking": tracking,
        "exit_mix": _exit_mix(trades),
        "equity": curve,
        "pending_open": pend_open[:12],
        "pending_count": len(pend_open),
        "recent": list(reversed(trades[-14:])),
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/api/state")
def api_state() -> JSONResponse:
    return JSONResponse(build_state())


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "db": str(DB_PATH), "exists": DB_PATH.exists()}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"state": build_state()}
    )
