"""Runner intelligence entrypoint — wires ingest + cluster into asyncio.gather."""
import asyncio

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tier import WalletTierCache
from runner.config.settings import get_settings
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.ingest.rpc_pool import RpcPool
from runner.ingest.transaction_parser import TransactionParser
from runner.ingest.wallet_monitor import WalletMonitor
from runner.utils.http import RateLimitedClient
from runner.utils.logging import configure_logging, get_logger


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("runner.main")
    logger.info("starting", log_level=settings.log_level)

    db = Database(settings.db_path)
    await db.connect()

    weights = WeightsLoader(settings.weights_yaml_path)

    registry = WalletRegistry(settings.wallets_json_path)
    registry.load()

    tier_cache = WalletTierCache(db)
    await tier_cache.load()

    http = RateLimitedClient(
        default_rps=weights.get("http_rate_limits.helius_rps", 10),
        per_host_rps={},
        timeout=15.0,
    )

    rpc_pool = RpcPool([settings.helius_rpc_url])
    parser = TransactionParser(rpc_pool, http)

    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()

    active = registry.active_addresses()
    wallets_map = {addr: registry.get(addr) for addr in active}

    monitor = WalletMonitor(
        wallets=wallets_map,
        event_bus=event_bus,
        parser=parser,
        ws_url=settings.helius_ws_url,
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        min_wallets=weights.get("cluster.min_wallets", 3),
        window_minutes=weights.get("cluster.window_minutes", 30),
    )

    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
    )

    try:
        await asyncio.gather(
            monitor.run(),
            detector.run(),
            _drain(signal_bus, logger),
        )
    finally:
        await http.aclose()
        await db.close()


async def _drain(signal_bus: asyncio.Queue, logger) -> None:
    """Phase 3 sink: log every signal. Phase 4 replaces this with enrichment."""
    while True:
        signal = await signal_bus.get()
        logger.info(
            "signal_drained",
            mint=signal.token_mint,
            wallets=signal.wallet_count,
            tier_counts=signal.tier_counts,
        )


if __name__ == "__main__":
    asyncio.run(_main())
