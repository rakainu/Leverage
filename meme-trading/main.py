"""SMC Trading System — Smart Money Convergence for Solana memecoins."""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from db.database import init_db, close_db
from scanner.wallet_monitor import WalletMonitor
from utils.logging import setup_logging


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

    # Verify RPC
    rpc_url = settings.solana_rpc_urls[0]
    helius = "helius" in rpc_url.lower()
    logger.info(f"RPC: {'Helius' if helius else 'Public'} ({rpc_url[:50]}...)")

    # Message buses
    event_bus = asyncio.Queue()  # BuyEvent: scanner -> engine

    # Scanner
    monitor = WalletMonitor(settings, event_bus)

    # Event consumer (temporary — prints events until convergence engine is built)
    async def event_logger():
        while True:
            event = await event_bus.get()
            logger.info(
                f"EVENT | {event.wallet_address[:8]}.. | "
                f"{event.token_mint[:8]}.. | "
                f"{event.amount_sol:.4f} SOL | {event.dex}"
            )

    logger.info("Starting scanner...")

    try:
        await asyncio.gather(
            monitor.run(),
            event_logger(),
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await close_db()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
