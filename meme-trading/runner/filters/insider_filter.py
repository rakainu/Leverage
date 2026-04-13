"""InsiderFilter — RugCheck /insiders/graph insider count → insider_risk sub-score.

Score bands (per runner spec):
  0-2 insiders: 100
  3-5:           85  (-15)
  6-10:          70  (-30)
  11+:           50  (-50, approaches a hard fail)
"""
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.insider_filter")

RUGCHECK_BASE = "https://api.rugcheck.xyz"


class InsiderFilter(BaseFilter):
    """Counts insider/linked wallets from RugCheck graph endpoint."""

    name = "insider_filter"

    def __init__(self, http: RateLimitedClient):
        self.http = http

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        graph = await self._fetch_graph(enriched.token_mint)
        if graph is None:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"insider_risk": 0.0},
                evidence={"errors": ["insiders_api_unavailable"]},
            )

        nodes = graph.get("nodes") or []
        count = len(nodes)

        if count <= 2:
            score = 100.0
        elif count <= 5:
            score = 85.0
        elif count <= 10:
            score = 70.0
        else:
            score = 50.0

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"insider_risk": score},
            evidence={
                "insider_count": count,
                "edge_count": len(graph.get("edges") or []),
            },
        )

    async def _fetch_graph(self, mint: str) -> dict[str, Any] | None:
        url = f"{RUGCHECK_BASE}/v1/tokens/{mint}/insiders/graph"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("insiders_graph_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None
        return data if isinstance(data, dict) else None
