"""SMC Trading System — Smart Money Convergence for Solana memecoins."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

import httpx
import uvicorn

from alerts.telegram import TelegramAlerter
from config.settings import Settings
from curation.pipeline import CurationPipeline
from dashboard.app import create_app
from dashboard.websocket_manager import WebSocketManager
from db.database import init_db, get_db, close_db
from engine.convergence import ConvergenceEngine
from engine.safety import SafetyChecker, SafetyResult
from engine.signal import ConvergenceSignal
from executor.paper import PaperExecutor
from executor.live import LiveExecutor
from executor.position_manager import PositionManager
from scanner.wallet_monitor import WalletMonitor
from utils.logging import setup_logging


async def signal_router(
    signal_bus: asyncio.Queue,
    alert_bus: asyncio.Queue,
    settings: Settings,
    logger,
):
    """Consume convergence signals, run safety checks, route to executor."""
    http = httpx.AsyncClient(timeout=30)
    safety = SafetyChecker(settings, http)
    paper = PaperExecutor(settings)
    live = None
    if settings.mode == "live":
        try:
            live = LiveExecutor(settings)
        except ValueError as e:
            logger.error(f"Live mode requested but failed to init: {e}")
            logger.warning("Falling back to paper mode")


    while True:
        signal: ConvergenceSignal = await signal_bus.get()

        # --- Convergence speed filter ---
        conv_min = signal.convergence_minutes
        if conv_min < settings.min_convergence_minutes or conv_min > settings.max_convergence_minutes:
            logger.info(
                f"SKIP {signal.token_mint[:12]}.. — convergence speed "
                f"{conv_min:.1f}min outside [{settings.min_convergence_minutes}-{settings.max_convergence_minutes}]min window"
            )
            db = await get_db()
            await db.execute(
                """UPDATE convergence_signals SET action_taken='skip_speed'
                   WHERE token_mint=? AND action_taken IS NULL
                   ORDER BY signal_at DESC LIMIT 1""",
                (signal.token_mint,),
            )
            await db.commit()
            continue

        # --- Time-of-day filter (UTC) ---
        current_hour = datetime.now(timezone.utc).hour
        if current_hour in settings.blocked_hours_utc:
            logger.info(
                f"SKIP {signal.token_mint[:12]}.. — blocked hour UTC {current_hour}"
            )
            db = await get_db()
            await db.execute(
                """UPDATE convergence_signals SET action_taken='skip_hour'
                   WHERE token_mint=? AND action_taken IS NULL
                   ORDER BY signal_at DESC LIMIT 1""",
                (signal.token_mint,),
            )
            await db.commit()
            continue

        logger.info(f"Running safety checks on {signal.token_mint[:12]}...")
        result: SafetyResult = await safety.check(signal.token_mint)

        # Update signal record with safety results
        db = await get_db()
        await db.execute(
            """UPDATE convergence_signals
               SET safety_passed=?, safety_details_json=?
               WHERE token_mint=? AND safety_passed IS NULL
               ORDER BY signal_at DESC LIMIT 1""",
            (
                1 if result.passed else 0,
                json.dumps({
                    "mint_authority_revoked": result.mint_authority_revoked,
                    "freeze_authority_revoked": result.freeze_authority_revoked,
                    "honeypot_risk": result.honeypot_risk,
                    "top_holder_pct": result.top_holder_pct,
                    "reasons": result.reasons,
                }),
                signal.token_mint,
            ),
        )
        await db.commit()

        # Push signal alert
        await alert_bus.put({
            "type": "convergence_signal",
            "token_mint": signal.token_mint,
            "token_symbol": signal.token_symbol,
            "wallet_count": len(signal.wallets),
            "total_amount_sol": round(signal.total_amount_sol, 4),
            "safety_passed": result.passed,
            "safety_reasons": result.reasons,
        })

        if not result.passed:
            logger.warning(f"Safety FAILED — skipping trade for {signal.token_mint[:12]}...")
            await db.execute(
                """UPDATE convergence_signals SET action_taken='safety_failed'
                   WHERE token_mint=? AND action_taken IS NULL
                   ORDER BY signal_at DESC LIMIT 1""",
                (signal.token_mint,),
            )
            await db.commit()
            continue

        # Check max concurrent positions
        open_count = await db.execute_fetchall(
            "SELECT COUNT(*) as cnt FROM positions WHERE status='open'"
        )
        if open_count and open_count[0]["cnt"] >= settings.max_concurrent_positions:
            logger.warning("Max concurrent positions reached — skipping")
            continue

        # Execute trade (paper or live)
        if settings.mode == "live" and live:
            position_id = await live.execute(signal, result)
            trade_mode = "live"
        else:
            position_id = await paper.execute(signal, result)
            trade_mode = "paper"

        if position_id:
            await alert_bus.put({
                "type": "position_opened",
                "position_id": position_id,
                "token_mint": signal.token_mint,
                "token_symbol": signal.token_symbol,
                "amount_sol": settings.trade_amount_sol,
                "mode": trade_mode,
                })


async def alert_fanout(alert_bus: asyncio.Queue, telegram_queue: asyncio.Queue, ws_manager: WebSocketManager):
    """Fan out alerts to both Telegram and WebSocket dashboard."""
    while True:
        alert = await alert_bus.get()
        await telegram_queue.put(alert)
        await ws_manager.broadcast(alert)


async def run_dashboard(settings: Settings, ws_manager: WebSocketManager, db):
    """Run the FastAPI dashboard server."""
    app = create_app(ws_manager, db)
    config = uvicorn.Config(
        app,
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    settings = Settings()
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("SMC Trading System starting...")
    logger.info(f"Mode: {settings.mode.upper()}")
    logger.info(f"Convergence: {settings.convergence_threshold} wallets / {settings.convergence_window_minutes}min window")
    logger.info(f"Trade size: {settings.trade_amount_sol} SOL")
    logger.info(f"SL: {settings.stop_loss_pct}% | Trail: activate@+{settings.trail_activate_pct}%, lock@+{settings.trail_breakeven_pct}%, distance {settings.trail_distance_pct}% | Timeout: {settings.position_timeout_minutes}min")
    logger.info(f"Convergence speed: {settings.min_convergence_minutes}-{settings.max_convergence_minutes}min | Blocked hours UTC: {settings.blocked_hours_utc}")
    logger.info(f"Dashboard: http://localhost:{settings.dashboard_port}")
    logger.info("=" * 60)

    # Initialize database
    await init_db()
    db = await get_db()
    logger.info("Database initialized")

    # Sync wallets.json to DB on startup
    curation_init = CurationPipeline(settings)
    await curation_init._sync_to_db()
    logger.info("Wallets synced to DB")

    # RPC info
    rpc_url = settings.solana_rpc_urls[0]
    helius = "helius" in rpc_url.lower()
    logger.info(f"RPC: {'Helius' if helius else 'Public'} ({rpc_url[:50]}...)")

    # Message buses
    event_bus = asyncio.Queue()     # BuyEvent: scanner -> convergence
    signal_bus = asyncio.Queue()    # ConvergenceSignal: convergence -> router
    alert_bus = asyncio.Queue()     # dict: router + positions -> fanout
    telegram_queue = asyncio.Queue()  # dict: fanout -> telegram

    # Components
    ws_manager = WebSocketManager()
    monitor = WalletMonitor(settings, event_bus)
    convergence = ConvergenceEngine(settings, event_bus, signal_bus, alert_bus)
    position_mgr = PositionManager(settings, alert_bus)
    telegram = TelegramAlerter(settings)
    curation = CurationPipeline(settings)

    logger.info("Starting all services...")

    try:
        await asyncio.gather(
            monitor.run(),
            convergence.run(),
            signal_router(signal_bus, alert_bus, settings, logger),
            position_mgr.run(),
            alert_fanout(alert_bus, telegram_queue, ws_manager),
            telegram.run(telegram_queue),
            run_dashboard(settings, ws_manager, db),
            curation.run_loop(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
