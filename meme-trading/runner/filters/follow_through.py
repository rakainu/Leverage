"""FollowThroughProbe — async 5-minute probe measuring cluster follow-through.

After a probe window, count additional A+B-tier wallets that bought
the same mint AND check whether price held. Score per the runner spec:

    +3 A+B wallets joined: 100
    +2:                     80
    +1:                     60
    0 joined, price within -5% of entry: 40
    0 joined, price up > 10%: 70
    Price dumps > 15%: 0 (dead cluster)
"""
import asyncio

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.follow_through")


class FollowThroughProbe(BaseFilter):
    """Wait, then count new A+B wallet buys and check price delta."""

    name = "follow_through"

    def __init__(
        self,
        db: Database,
        tier_cache: WalletTierCache,
        price_fetcher,
        probe_minutes: float = 5.0,
    ):
        self.db = db
        self.tier_cache = tier_cache
        self.price_fetcher = price_fetcher
        self.probe_minutes = probe_minutes

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        # Sleep for the probe window (probe_minutes=0 for tests).
        await asyncio.sleep(self.probe_minutes * 60.0)

        new_ab_wallets = await self._count_new_ab_wallets(enriched)
        current_price = await self._current_price(enriched.token_mint)

        cluster_price = enriched.cluster_signal.mid_price_sol
        price_delta_pct: float | None = None
        if current_price is not None and cluster_price > 0:
            price_delta_pct = ((current_price - cluster_price) / cluster_price) * 100.0

        score = self._score(new_ab_wallets, price_delta_pct)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"follow_through": score},
            evidence={
                "new_ab_wallets": new_ab_wallets,
                "price_delta_pct": price_delta_pct,
                "probe_minutes": self.probe_minutes,
            },
        )

    def _score(
        self, new_ab_wallets: int, price_delta_pct: float | None
    ) -> float:
        # Price dump hard-zero
        if price_delta_pct is not None and price_delta_pct < -15.0:
            return 0.0

        if new_ab_wallets >= 3:
            return 100.0
        if new_ab_wallets == 2:
            return 80.0
        if new_ab_wallets == 1:
            return 60.0

        # No new wallets — fall back to price action
        if price_delta_pct is None:
            return 40.0  # neutral when price unknown
        if price_delta_pct > 10.0:
            return 70.0
        if price_delta_pct >= -5.0:
            return 40.0
        return 20.0  # -5% to -15%, weakening

    async def _count_new_ab_wallets(self, enriched: EnrichedToken) -> int:
        assert self.db.conn is not None
        cutoff = enriched.cluster_signal.last_buy_time.isoformat()
        cluster_wallets = set(enriched.cluster_signal.wallets)

        async with self.db.conn.execute(
            """
            SELECT DISTINCT wallet_address FROM buy_events
            WHERE token_mint = ?
              AND block_time > ?
            """,
            (enriched.token_mint, cutoff),
        ) as cur:
            rows = await cur.fetchall()

        new_wallets = [
            w for (w,) in rows
            if w not in cluster_wallets
            and self.tier_cache.tier_of(w) in (Tier.A, Tier.B)
        ]
        return len(new_wallets)

    async def _current_price(self, mint: str) -> float | None:
        try:
            result = await self.price_fetcher.fetch(mint)
        except Exception as e:  # noqa: BLE001
            logger.warning("follow_through_price_error", mint=mint, error=str(e))
            return None
        if result is None:
            return None
        return result.get("price_sol")
