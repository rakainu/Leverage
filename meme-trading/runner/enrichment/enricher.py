"""Enrichment orchestrator — turns a ClusterSignal into an EnrichedToken."""
import asyncio
from datetime import datetime, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.enricher")


class Enricher:
    """Run the three enrichment fetchers in parallel and assemble an EnrichedToken.

    Sub-fetcher failures are collected into `EnrichedToken.errors` rather
    than raised, so a single slow/broken API cannot block the pipeline.
    """

    def __init__(
        self,
        signal_bus: asyncio.Queue,
        enriched_bus: asyncio.Queue,
        metadata_fetcher,
        price_fetcher,
        deployer_fetcher,
    ):
        self.signal_bus = signal_bus
        self.enriched_bus = enriched_bus
        self.metadata_fetcher = metadata_fetcher
        self.price_fetcher = price_fetcher
        self.deployer_fetcher = deployer_fetcher

    async def run(self) -> None:
        logger.info("enricher_start")
        while True:
            signal: ClusterSignal = await self.signal_bus.get()
            try:
                enriched = await self._enrich_one(signal)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "enricher_unexpected_failure",
                    mint=signal.token_mint,
                    error=str(e),
                )
                continue
            await self.enriched_bus.put(enriched)

    async def _enrich_one(self, signal: ClusterSignal) -> EnrichedToken:
        meta_task = asyncio.create_task(self.metadata_fetcher.fetch(signal.token_mint))
        price_task = asyncio.create_task(self.price_fetcher.fetch(signal.token_mint))
        deployer_task = asyncio.create_task(self.deployer_fetcher.fetch(signal.token_mint))

        meta, price, deployer = await asyncio.gather(
            meta_task, price_task, deployer_task, return_exceptions=True
        )

        errors: list[str] = []

        meta = None if isinstance(meta, Exception) or meta is None else meta
        if meta is None:
            errors.append("metadata_unavailable")

        price = None if isinstance(price, Exception) or price is None else price
        if price is None:
            errors.append("price_liquidity_unavailable")

        deployer = (
            None if isinstance(deployer, Exception) or deployer is None else deployer
        )
        if deployer is None:
            errors.append("deployer_unavailable")

        return EnrichedToken(
            token_mint=signal.token_mint,
            cluster_signal=signal,
            enriched_at=datetime.now(timezone.utc),
            symbol=(meta or {}).get("symbol"),
            name=(meta or {}).get("name"),
            decimals=(meta or {}).get("decimals"),
            supply=(meta or {}).get("supply"),
            token_created_at=None,
            mint_authority=(meta or {}).get("mint_authority"),
            freeze_authority=(meta or {}).get("freeze_authority"),
            price_sol=(price or {}).get("price_sol"),
            price_usd=(price or {}).get("price_usd"),
            liquidity_usd=(price or {}).get("liquidity_usd"),
            volume_24h_usd=(price or {}).get("volume_24h_usd"),
            pair_age_seconds=(price or {}).get("pair_age_seconds"),
            slippage_at_size_pct=(price or {}).get("slippage_at_size_pct", {}),
            deployer_address=(deployer or {}).get("deployer_address"),
            deployer_age_seconds=(deployer or {}).get("deployer_age_seconds"),
            deployer_token_count=(deployer or {}).get("deployer_token_count"),
            errors=errors,
            cluster_signal_id=signal.id,
        )
