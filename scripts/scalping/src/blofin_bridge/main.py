"""FastAPI app: webhook entry point, health, status."""
from __future__ import annotations
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .blofin_client import BloFinClient, build_ccxt_client
from .config import Settings, load_config
from .notify import (
    Notifier, format_entry, format_sl_close, format_reversal,
    format_error, format_pending,
)
from .poller import PositionPoller
from .router import dispatch, UnknownAction
from .state import Store

log = logging.getLogger(__name__)


class WebhookPayload(BaseModel):
    secret: str
    symbol: str
    action: Literal[
        "buy", "sell", "sl",
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

    from .reconcile import reconcile
    rec_report = reconcile(store=store, blofin=blofin)
    frozen: set[str] = set(rec_report.frozen_symbols)
    if rec_report.drift_count > 0:
        notifier.send(
            "RECONCILE DRIFT on startup: "
            + "; ".join(rec_report.details)
            + " — frozen: " + ", ".join(rec_report.frozen_symbols)
        )

    symbol_configs = {
        name: {
            **sc.model_dump(),
            "sl_loss_usdt": settings.defaults.sl_loss_usdt,
            "trail_activate_usdt": settings.defaults.trail_activate_usdt,
            "trail_distance_usdt": settings.defaults.trail_distance_usdt,
            "tp_limit_margin_pct": settings.defaults.tp_limit_margin_pct,
            "ema_retest_timeout_minutes": settings.defaults.ema_retest_timeout_minutes,
        }
        for name, sc in settings.symbols.items()
    }

    poller = PositionPoller(
        store=store,
        blofin=blofin,
        interval_seconds=settings.defaults.poll_interval_seconds,
        breakeven_usdt=settings.defaults.breakeven_usdt,
        trail_activate_usdt=settings.defaults.trail_activate_usdt,
        trail_start_usdt=settings.defaults.trail_start_usdt,
        trail_distance_usdt=settings.defaults.trail_distance_usdt,
        margin_usdt=settings.defaults.margin_usdt,
        leverage=settings.defaults.leverage,
        notifier=notifier,
        ema_retest_period=settings.defaults.ema_retest_period,
        ema_retest_timeframe=settings.defaults.ema_retest_timeframe,
        symbol_configs=symbol_configs,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poller.start()
        try:
            yield
        finally:
            await poller.stop()

    app = FastAPI(
        title="BloFin × TradingView Bridge",
        version="0.1.1",
        lifespan=lifespan,
    )

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

    @app.get("/trades")
    def trades(secret: str = "", limit: int = 50) -> dict[str, Any]:
        if secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")
        return {"trades": store.get_trade_log(limit=limit)}

    def _process_webhook(payload: WebhookPayload, raw: bytes, event_id: int) -> None:
        """Process webhook in background so TV doesn't timeout."""
        try:
            result = dispatch(
                action=payload.action, symbol=payload.symbol,
                store=store, blofin=blofin, symbol_configs=symbol_configs,
            )
            store.mark_event_handled(event_id, outcome="ok", error_msg=None)

            result["symbol"] = payload.symbol
            if result.get("pending"):
                notifier.send(format_pending(
                    result.get("action", payload.action),
                    payload.symbol,
                    result.get("signal_price", 0),
                ))
            elif payload.action in ("buy", "sell") and result.get("opened"):
                notifier.send(format_entry(result))
            elif payload.action == "sl":
                notifier.send(format_sl_close(result, payload.symbol))
            elif payload.action.startswith("reversal_"):
                if result.get("pending_new"):
                    notifier.send(format_pending(
                        result.get("action", "buy"),
                        payload.symbol,
                        result.get("signal_price", 0),
                    ))
                elif result.get("opened_new"):
                    notifier.send(format_reversal(result, payload.symbol))
            else:
                notifier.send(f"ℹ️ {payload.action.upper()} {payload.symbol}: done")
        except UnknownAction as exc:
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=f"unknown action {exc}")
        except Exception as exc:
            log.exception("handler failed")
            store.mark_event_handled(event_id, outcome="error",
                                     error_msg=str(exc))
            notifier.send(format_error(payload.action, payload.symbol, str(exc)))

    @app.post("/webhook/pro-v3")
    async def pro_v3(request: Request) -> dict[str, Any]:
        raw = await request.body()
        try:
            payload = WebhookPayload(**json.loads(raw or b"{}"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"bad payload: {exc}")

        if payload.secret != settings.bridge.shared_secret:
            raise HTTPException(status_code=401, detail="invalid secret")

        if payload.symbol in frozen:
            skipped_id = store.append_event(
                position_id=None, event_type=payload.action,
                payload=raw.decode("utf-8"),
            )
            store.mark_event_handled(skipped_id, outcome="skipped",
                                     error_msg="symbol frozen after reconcile drift")
            raise HTTPException(status_code=423, detail="symbol frozen")

        event_id = store.append_event(
            position_id=None, event_type=payload.action,
            payload=raw.decode("utf-8"),
        )

        # Respond immediately so TradingView doesn't timeout,
        # then process the trade in the background.
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _process_webhook, payload, raw, event_id)

        return {"accepted": True, "action": payload.action, "symbol": payload.symbol}

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
