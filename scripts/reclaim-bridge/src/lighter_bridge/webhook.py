"""Inbound Pro V3 [SMRT Algo] webhook listener.

When signal_source == "webhook", the bridge runs this FastAPI app alongside its
async loops. TradingView Pro V3 buy/sell alerts POST here; each valid signal is
pushed onto an asyncio.Queue the bridge drains into its EMA-retest pending queue
— the exact same entry pipeline used by the live BloFin bridge, so entries match
what was validated in pro_v3_real/.

Payload (matches the BloFin bridge, see scripts/scalping/docs/TV_ALERTS.md):
  {"secret": "...", "symbol": "SOL-USDT", "action": "buy"|"sell", "source": "pro_v3"}
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("webhook")


ALLOWED_ACTIONS = {"buy", "sell", "tp1", "tp2", "tp3", "sl",
                   "reversal_buy", "reversal_sell"}


@dataclass
class InboundSignal:
    symbol_key: str   # bridge symbol name, e.g. "SOL"
    action: str       # buy|sell|tp1|tp2|tp3|sl|reversal_buy|reversal_sell


def _symbol_key(raw_symbol: str, known: set[str]) -> str | None:
    """Map a webhook symbol ('SOL-USDT', 'SOLUSDT.P', 'SOL') to a config key."""
    s = raw_symbol.upper()
    if s in known:
        return s
    base = s.replace("-USDT", "").replace("USDT.P", "").replace("USDT", "").replace(".P", "")
    return base if base in known else None


def build_app(queue: "asyncio.Queue[InboundSignal]", secret: str,
              known_symbols: set[str], path: str) -> FastAPI:
    app = FastAPI(title="pro-v3-scaleout webhook")

    @app.get("/health")
    async def health():
        return {"ok": True, "symbols": sorted(known_symbols)}

    @app.post(path)
    async def webhook(req: Request):
        try:
            body = await req.json()
        except Exception:
            return JSONResponse({"error": "bad json"}, status_code=400)
        if secret and body.get("secret") != secret:
            return JSONResponse({"error": "invalid secret"}, status_code=401)
        action = str(body.get("action", "")).lower()
        if action not in ALLOWED_ACTIONS:
            return JSONResponse({"error": f"action '{action}' not allowed"}, status_code=400)
        key = _symbol_key(str(body.get("symbol", "")), known_symbols)
        if key is None:
            return JSONResponse({"error": f"symbol '{body.get('symbol')}' not enabled"},
                                status_code=400)
        await queue.put(InboundSignal(symbol_key=key, action=action))
        log.info("webhook: %s %s queued", key, action)
        return {"queued": True, "symbol": key, "action": action}

    return app


async def run_server(app: FastAPI, host: str, port: int):
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    await server.serve()
