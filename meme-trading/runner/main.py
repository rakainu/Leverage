"""Runner intelligence entrypoint — wires ingest + cluster into asyncio.gather."""
import asyncio
from urllib.parse import urlparse

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

    helius_host = urlparse(settings.helius_rpc_url).netloc.lower()
    helius_rps = weights.get("http_rate_limits.helius_rps", 10)

    http = RateLimitedClient(
        default_rps=helius_rps,
        per_host_rps={helius_host: helius_rps} if helius_host else {},
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
        db=db,
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        db=db,
        weights=weights,
    )

    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
        helius_host=helius_host,
        helius_rps=helius_rps,
    )

    try:
        results = await asyncio.gather(
            _supervise(monitor.run, "wallet_monitor", logger),
            _supervise(detector.run, "convergence_detector", logger),
            _supervise(lambda: _drain(signal_bus, logger), "drain", logger),
            return_exceptions=True,
        )
        for name, result in zip(["monitor", "detector", "drain"], results):
            if isinstance(result, Exception):
                logger.error("task_exited_with_exception", task=name, error=str(result))
    finally:
        await http.aclose()
        await db.close()


async def _supervise(factory, name: str, logger) -> None:
    """Run a long-lived task forever, restarting it on unexpected exceptions.

    `factory` is a zero-arg callable that returns the coroutine to await.
    Most tasks we run here are infinite loops — if one exits via raise,
    we log and restart with an exponential backoff cap.
    """
    backoff = 1.0
    while True:
        try:
            await factory()
            # Factory returned cleanly — treat as an intentional exit.
            logger.info("task_exit_clean", task=name)
            return
        except asyncio.CancelledError:
            logger.info("task_cancelled", task=name)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(
                "task_crashed_restarting",
                task=name,
                error=str(e),
                backoff=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 60.0)


async def _drain(signal_bus: asyncio.Queue, logger) -> None:
    """Phase 3 sink: log every signal. Replaced by Enricher in Task 11.

    Wrapped in per-iteration try/except so a bad signal can't kill the process.
    """
    while True:
        try:
            signal = await signal_bus.get()
            logger.info(
                "signal_drained",
                mint=signal.token_mint,
                wallets=signal.wallet_count,
                tier_counts=signal.tier_counts,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_iteration_error", error=str(e))


if __name__ == "__main__":
    asyncio.run(_main())
