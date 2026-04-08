"""FastAPI app: webhook entry point, health, status."""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .blofin_client import BloFinClient, build_ccxt_client
from .config import Settings, load_config
from .notify import Notifier
from .router import dispatch, UnknownAction
from .state import Store

log = logging.getLogger(__name__)


class WebhookPayload(BaseModel):
    secret: str
    symbol: str
    action: Literal[
        "buy", "sell", "tp1", "tp2", "tp3", "sl",
        "reversal_buy", "reversal_sell",
    ]
    source: str = Field(default="pro_v3")


def _build_blofin_client(settings: Settings) -> BloFinClient:
    """Factory kept as a module-level function so tests can monkeypatch it."""
    ccxt_client = build_ccxt_client(
        api_key=settings.blofin.api_key,
        secret=settings.blofin.api_secret,
        passphrase=settings.blofin.passphrase,
        env=settings.blofin.env,
    )
    client = BloFinClient(ccxt_client=ccxt_client)
    client.load_instruments()
    return client


def create_app() -> FastAPI:
    config_path = Path(
        os.environ.get("BLOFIN_BRIDGE_CONFIG")
        or (Path(__file__).resolve().parents[3] / "config" / "blofin_bridge.yaml")
    )
    db_path = Path(
        os.environ.get("BLOFIN_BRIDGE_DB")
        or (Path(__file__).resolve().parents[3] / "data" / "bridge.db")
    )
    settings = load_config(config_path)
    store = Store(db_path)
    blofin = _build_blofin_client(settings)
    notifier = Notifier(
        bot_token=settings.bridge.telegram_bot_token,
        chat_id=settings.bridge.telegram_chat_id,
    )

    app = FastAPI(title="BloFin × TradingView Bridge", version="0.1.0")

    symbol_configs = {
        name: {
            **sc.model_dump(),
            "safety_sl_pct": settings.defaults.safety_sl_pct,
            "tp_split": settings.defaults.tp_split,
        }
        for name, sc in settings.symbols.items()
    }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "env": settings.blofin.env,
            "enabled_symbols": [
                s for s, c in settings.symbols.items() if c.enabled
            ],
            "open_positions": len(store.list_open_positions()),
        }

    @app.get("/status")
    def status(secret: str = "") -> dict[str, Any]:
        if secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")
        return {
            "open_positions": [
                {
                    "id": p.id, "symbol": p.symbol, "side": p.side,
                    "entry_price": p.entry_price,
                    "current_size": p.current_size,
                    "tp_stage": p.tp_stage,
                    "sl_order_id": p.sl_order_id,
                }
                for p in store.list_open_positions()
            ],
            "recent_events": store.recent_events(limit=20),
        }

    @app.post("/webhook/pro-v3")
    async def pro_v3(request: Request) -> dict[str, Any]:
        raw = await request.body()
        try:
            payload = WebhookPayload(**json.loads(raw or b"{}"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"bad payload: {exc}")

        if payload.secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")

        event_id = store.append_event(
            position_id=None, event_type=payload.action,
            payload=raw.decode("utf-8"),
        )

        try:
            result = dispatch(
                action=payload.action, symbol=payload.symbol,
                store=store, blofin=blofin, symbol_configs=symbol_configs,
            )
            store.mark_event_handled(event_id, outcome="ok", error_msg=None)
            notifier.send(f"{payload.action.upper()} {payload.symbol}: {result}")
            return {"result": result}
        except UnknownAction as exc:
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=f"unknown action {exc}")
            raise HTTPException(status_code=400, detail=f"unknown action {exc}")
        except Exception as exc:
            log.exception("handler failed")
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=str(exc))
            notifier.send(f"ERROR: {payload.action} {payload.symbol}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    return app


def run() -> None:
    import uvicorn
    uvicorn.run(
        create_app(),
        host=os.environ.get("BLOFIN_BRIDGE_HOST", "0.0.0.0"),
        port=int(os.environ.get("BLOFIN_BRIDGE_PORT", "8787")),
    )


if __name__ == "__main__":
    run()
