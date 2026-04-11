"""Runner intelligence entrypoint — wires ingest + cluster into asyncio.gather."""
import asyncio
from urllib.parse import urlparse

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tier import WalletTierCache
from runner.config.settings import get_settings
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.deployer import DeployerFetcher
from runner.enrichment.enricher import Enricher
from runner.enrichment.price_liquidity import PriceLiquidityFetcher
from runner.enrichment.token_metadata import TokenMetadataFetcher
from runner.filters.entry_quality import EntryQualityFilter
from runner.filters.follow_through import FollowThroughProbe
from runner.filters.holder_filter import HolderFilter
from runner.filters.insider_filter import InsiderFilter
from runner.filters.pipeline import FilterPipeline
from runner.filters.rug_gate import RugGate
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
    dexscreener_rps = weights.get("http_rate_limits.dexscreener_rps", 3)
    jupiter_rps = weights.get("http_rate_limits.jupiter_rps", 5)

    per_host_rps: dict[str, float] = {
        "api.dexscreener.com": dexscreener_rps,
        "quote-api.jup.ag": jupiter_rps,
    }
    if helius_host:
        per_host_rps[helius_host] = helius_rps

    http = RateLimitedClient(
        default_rps=helius_rps,
        per_host_rps=per_host_rps,
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

    metadata_fetcher = TokenMetadataFetcher(http, rpc_url=settings.helius_rpc_url)
    price_fetcher = PriceLiquidityFetcher(http)
    deployer_fetcher = DeployerFetcher(http, rpc_url=settings.helius_rpc_url)

    enriched_bus: asyncio.Queue = asyncio.Queue()
    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata_fetcher,
        price_fetcher=price_fetcher,
        deployer_fetcher=deployer_fetcher,
    )

    rug_gate = RugGate(
        http,
        lp_locked_pct_min=weights.get("gates.lp_locked_pct_min", 85),
    )
    holder_filter = HolderFilter(
        http,
        rpc_url=settings.helius_rpc_url,
        top10_max_pct=weights.get("gates.top10_max_pct", 70),
    )
    insider_filter = InsiderFilter(http)
    entry_quality_filter = EntryQualityFilter()
    follow_through_probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=weights.get("probe.follow_through_minutes", 5),
    )

    filtered_bus: asyncio.Queue = asyncio.Queue()
    filter_pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=[rug_gate, holder_filter, insider_filter, entry_quality_filter],
        probe_filter=follow_through_probe,
        db=db,
    )

    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
        helius_host=helius_host,
        helius_rps=helius_rps,
        dexscreener_rps=dexscreener_rps,
        jupiter_rps=jupiter_rps,
    )

    try:
        results = await asyncio.gather(
            _supervise(monitor.run, "wallet_monitor", logger),
            _supervise(detector.run, "convergence_detector", logger),
            _supervise(enricher.run, "enricher", logger),
            _supervise(filter_pipeline.run, "filter_pipeline", logger),
            _supervise(lambda: _drain_filtered(filtered_bus, logger), "drain_filtered", logger),
            return_exceptions=True,
        )
        for name, result in zip(
            ["monitor", "detector", "enricher", "filter_pipeline", "drain_filtered"],
            results,
        ):
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


async def _drain_filtered(filtered_bus: asyncio.Queue, logger) -> None:
    """Phase 5 sink: log every filtered candidate. Replaced by Scoring engine in Plan 2c."""
    while True:
        try:
            fc = await filtered_bus.get()
            logger.info(
                "filtered_candidate_drained",
                mint=fc.enriched.token_mint,
                symbol=fc.enriched.symbol,
                gate_passed=fc.gate_passed,
                hard_fail_reason=fc.hard_fail_reason,
                filter_count=len(fc.filter_results),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_filtered_iteration_error", error=str(e))


if __name__ == "__main__":
    asyncio.run(_main())
