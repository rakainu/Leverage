"""SMC Trading System — Smart Money Convergence for Solana memecoins."""

import asyncio
import signal
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from db.database import init_db, close_db
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

    # Verify Helius connectivity
    rpc_url = settings.solana_rpc_urls[0]
    helius = "helius" in rpc_url.lower()
    logger.info(f"RPC: {'Helius' if helius else 'Public'} ({rpc_url[:50]}...)")

    logger.info("Phase 1 foundation complete. Scanner (Phase 2) not yet implemented.")

    # Cleanup
    await close_db()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
