"""HolderFilter — Helius DAS getTokenAccounts with top-10 concentration gate."""
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.holder_filter")


class HolderFilter(BaseFilter):
    """Computes holder count and top-10 concentration.

    Hard gate: top-10 holder concentration (excluding the deployer address,
    if known) > top10_max_pct is a hard fail (typically 70%).

    Sub-score `holder_quality` (0-100):
      + 30 for > 100 unique holders, + 20 for 50-100, + 10 for 20-50
      + 30 for top-10 < 30%, + 20 for 30-45%, + 10 for 45-60%, + 0 for >= 60%
    (max 100 — two 30s and an excess 20 equal 80; we cap.)
    """

    name = "holder_filter"

    def __init__(
        self,
        http: RateLimitedClient,
        rpc_url: str,
        top10_max_pct: float = 70.0,
    ):
        self.http = http
        self.rpc_url = rpc_url
        self.top10_max_pct = top10_max_pct

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        accounts = await self._fetch_token_accounts(enriched.token_mint)
        if accounts is None:
            return FilterResult(
                filter_name=self.name,
                passed=True,  # API failure is not a hard fail
                hard_fail_reason=None,
                sub_scores={"holder_quality": 0.0},
                evidence={"errors": ["das_api_unavailable"]},
            )

        # Exclude deployer from holder set (common for dev-owned supply).
        deployer = enriched.deployer_address
        filtered = [
            a for a in accounts
            if a.get("owner") and a.get("owner") != deployer
        ]

        total_supply = sum(int(a.get("amount") or 0) for a in filtered)
        if total_supply == 0:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"holder_quality": 0.0},
                evidence={"unique_holders": 0, "top10_pct": 0.0},
            )

        # Sort by balance descending
        filtered.sort(key=lambda a: int(a.get("amount") or 0), reverse=True)
        unique_holders = len({a.get("owner") for a in filtered})

        top10 = filtered[:10]
        top10_balance = sum(int(a.get("amount") or 0) for a in top10)
        top10_pct = (top10_balance / total_supply) * 100.0

        if top10_pct > self.top10_max_pct:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason=f"top-10 holders hold {top10_pct:.1f}% > {self.top10_max_pct}%",
                sub_scores={"holder_quality": 0.0},
                evidence={
                    "unique_holders": unique_holders,
                    "top10_pct": top10_pct,
                },
            )

        score = 0.0
        if unique_holders > 100:
            score += 30
        elif unique_holders >= 50:
            score += 20
        elif unique_holders >= 20:
            score += 10

        if top10_pct < 30:
            score += 30
        elif top10_pct < 45:
            score += 20
        elif top10_pct < 60:
            score += 10

        score = min(100.0, score)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"holder_quality": score},
            evidence={
                "unique_holders": unique_holders,
                "top10_pct": top10_pct,
                "total_supply": total_supply,
            },
        )

    async def _fetch_token_accounts(self, mint: str) -> list[dict] | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccounts",
                    "params": {"mint": mint, "limit": 1000},
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getTokenAccounts_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None

        result = body.get("result")
        if not result or not isinstance(result, dict):
            return None
        accounts = result.get("token_accounts") or []
        return accounts if isinstance(accounts, list) else None
