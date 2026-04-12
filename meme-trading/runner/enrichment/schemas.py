"""EnrichedToken dataclass — the unit the filter/scoring pipeline consumes."""
from dataclasses import dataclass, field
from datetime import datetime

from runner.cluster.convergence import ClusterSignal


@dataclass(frozen=True, eq=False)
class EnrichedToken:
    """A cluster signal expanded with metadata, price, liquidity, and deployer info.

    Required fields are the minimum a candidate must have to flow through
    downstream filters; optional fields are populated by the enrichment
    sub-fetchers when they succeed. Each sub-fetcher failure adds an entry
    to `errors` instead of raising, so one slow/broken API can't sink a
    candidate.
    """

    token_mint: str
    cluster_signal: ClusterSignal
    enriched_at: datetime

    # Metadata (Helius DAS)
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = None
    supply: float | None = None
    token_created_at: datetime | None = None
    # Authority flags — None means unknown, "revoked" is represented as None/empty
    # and any non-empty/non-null value means the authority still exists (rug risk).
    mint_authority: str | None = None
    freeze_authority: str | None = None

    # Price / liquidity (DexScreener + Jupiter)
    price_sol: float | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    pair_age_seconds: int | None = None
    slippage_at_size_pct: dict[float, float] = field(default_factory=dict)

    # Deployer history (Helius)
    deployer_address: str | None = None
    deployer_age_seconds: int | None = None
    deployer_token_count: int | None = None

    # Per-fetcher failures, collected non-fatally
    errors: list[str] = field(default_factory=list)

    # Pipeline threading — links back to the originating cluster_signals row
    cluster_signal_id: int | None = None
