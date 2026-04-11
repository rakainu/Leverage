"""EntryQualityFilter — pure computation on EnrichedToken fields."""
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult


class EntryQualityFilter(BaseFilter):
    """Scores how good the entry point looks.

    Three factors:
    1. Price extension since cluster mid-price (anti-chase):
       < 5% → 100, 5-15% → 75, 15-30% → 45, 30-60% → 15, > 60% → 0
    2. Token freshness modifier (added to extension score):
       < 30m → +15, 30m-2h → +10, 2-6h → 0, 6-24h → -10, > 24h → -20
    3. Liquidity depth check: if 0.25 SOL slippage > 5%, cap score at 40.
    """

    name = "entry_quality"

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        current_price = enriched.price_sol
        cluster_price = enriched.cluster_signal.mid_price_sol

        if current_price is None or cluster_price is None or cluster_price <= 0:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"entry_quality": 0.0},
                evidence={"errors": ["missing_current_price"]},
            )

        extension_pct = ((current_price - cluster_price) / cluster_price) * 100.0
        # Anti-chase: we care about upward extension only.
        if extension_pct < 5.0:
            score = 100.0
        elif extension_pct < 15.0:
            score = 75.0
        elif extension_pct < 30.0:
            score = 45.0
        elif extension_pct < 60.0:
            score = 15.0
        else:
            score = 0.0

        age_seconds = enriched.pair_age_seconds
        freshness_mod = 0.0
        if age_seconds is not None:
            if age_seconds < 30 * 60:
                freshness_mod = 15.0
            elif age_seconds < 2 * 3600:
                freshness_mod = 10.0
            elif age_seconds < 6 * 3600:
                freshness_mod = 0.0
            elif age_seconds < 24 * 3600:
                freshness_mod = -10.0
            else:
                freshness_mod = -20.0

        score = score + freshness_mod
        score = max(0.0, min(100.0, score))

        # Liquidity-depth cap
        slippage_025 = enriched.slippage_at_size_pct.get(0.25)
        if slippage_025 is not None and slippage_025 > 5.0:
            score = min(score, 40.0)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"entry_quality": score},
            evidence={
                "extension_pct": extension_pct,
                "pair_age_seconds": age_seconds,
                "slippage_25": slippage_025,
            },
        )
