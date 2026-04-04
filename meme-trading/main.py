"""SMC Trading System — Smart Money Convergence for Solana memecoins."""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

import httpx

from config.settings import Settings
from db.database import init_db, get_db, close_db
from engine.convergence import ConvergenceEngine
from engine.safety import SafetyChecker, SafetyResult
from engine.signal import ConvergenceSignal
from executor.paper import PaperExecutor
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

    while True:
        signal: ConvergenceSignal = await signal_bus.get()

        # Run safety checks
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

        # Execute paper trade
        if settings.mode == "paper":
            position_id = await paper.execute(signal, result)
            if position_id:
                await alert_bus.put({
                    "type": "position_opened",
                    "position_id": position_id,
                    "token_mint": signal.token_mint,
                    "token_symbol": signal.token_symbol,
                    "entry_price": 0,  # Will be set by executor
                    "amount_sol": settings.trade_amount_sol,
                    "mode": "paper",
                })


async def main():
    settings = Settings()
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("SMC Trading System starting...")
    logger.info(f"Mode: {settings.mode.upper()}")
    logger.info(f"Convergence: {settings.convergence_threshold} wallets / {settings.convergence_window_minutes}min window")
    logger.info(f"Trade size: {settings.trade_amount_sol} SOL")
    logger.info(f"TP: {settings.take_profit_pct}% | SL: {settings.stop_loss_pct}% | Timeout: {settings.position_timeout_minutes}min")
    logger.info(f"Dashboard: http://localhost:{settings.dashboard_port}")
    logger.info("=" * 60)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # RPC info
    rpc_url = settings.solana_rpc_urls[0]
    helius = "helius" in rpc_url.lower()
    logger.info(f"RPC: {'Helius' if helius else 'Public'} ({rpc_url[:50]}...)")

    # Message buses
    event_bus = asyncio.Queue()   # BuyEvent: scanner -> convergence engine
    signal_bus = asyncio.Queue()  # ConvergenceSignal: engine -> signal_router
    alert_bus = asyncio.Queue()   # dict: signal_router + position_manager -> alerts

    # Alert consumer (temporary — logs alerts until dashboard/telegram is built)
    async def alert_logger():
        while True:
            alert = await alert_bus.get()
            alert_type = alert.get("type", "unknown")
            logger.info(f"ALERT [{alert_type}]: {json.dumps(alert, default=str)}")

    # Components
    monitor = WalletMonitor(settings, event_bus)
    convergence = ConvergenceEngine(settings, event_bus, signal_bus)
    position_mgr = PositionManager(settings, alert_bus)

    logger.info("Starting all services...")

    try:
        await asyncio.gather(
            monitor.run(),
            convergence.run(),
            signal_router(signal_bus, alert_bus, settings, logger),
            position_mgr.run(),
            alert_logger(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
