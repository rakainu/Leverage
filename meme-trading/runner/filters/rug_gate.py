"""RugGate filter — RugCheck /report/summary-based hard gates + rug_risk sub-score."""
import asyncio
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.rug_gate")

RUGCHECK_BASE = "https://api.rugcheck.xyz"
RUGCHECK_RETRY_DELAY_SECONDS = 5.0


class RugGate(BaseFilter):
    """Checks RugCheck report summary + EnrichedToken authority fields.

    Hard gates (any failure → passed=False):
      1. Mint authority must be revoked (EnrichedToken.mint_authority is None)
      2. Freeze authority must be revoked (EnrichedToken.freeze_authority is None)
      3. RugCheck data must be available (one retry on indexer lag, then fail closed)
      4. LP locked % must be >= lp_locked_pct_min

    Sub-score: `rug_risk` starts at 100, subtracts RugCheck score_normalised
    directly, subtracts 5 per `warn` risk entry (cap -30 on risks alone).
    """

    name = "rug_gate"

    def __init__(
        self,
        http: RateLimitedClient,
        lp_locked_pct_min: float = 85.0,
    ):
        self.http = http
        self.lp_locked_pct_min = lp_locked_pct_min

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        # Check authority fields first — these come from Plan 2a metadata fetch
        if enriched.mint_authority is not None:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason="mint authority not revoked",
                sub_scores={"rug_risk": 0.0},
                evidence={"mint_authority": enriched.mint_authority},
            )
        if enriched.freeze_authority is not None:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason="freeze authority not revoked",
                sub_scores={"rug_risk": 0.0},
                evidence={"freeze_authority": enriched.freeze_authority},
            )

        # Fetch RugCheck summary — one short retry to absorb indexer lag on fresh mints
        summary = await self._fetch_summary(enriched.token_mint)
        if summary is None:
            await asyncio.sleep(RUGCHECK_RETRY_DELAY_SECONDS)
            summary = await self._fetch_summary(enriched.token_mint)
        if summary is None:
            # Conservative: no rug data → don't trade. Better than silently passing.
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason="rugcheck_unavailable_after_retry",
                sub_scores={"rug_risk": 0.0},
                evidence={
                    "errors": ["rugcheck_api_unavailable"],
                    "retry_attempted": True,
                },
            )

        lp_locked_pct = float(summary.get("lpLockedPct") or 0.0)
        if lp_locked_pct < self.lp_locked_pct_min:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason=f"lp locked pct {lp_locked_pct:.1f} below min {self.lp_locked_pct_min}",
                sub_scores={"rug_risk": 0.0},
                evidence={"lp_locked_pct": lp_locked_pct},
            )

        # Compute rug_risk sub-score
        score = 100.0
        score_normalised = float(summary.get("score_normalised") or 0.0)
        score -= score_normalised

        risks = summary.get("risks") or []
        warn_risks = [r for r in risks if r.get("level") == "warn"]
        penalty = min(5.0 * len(warn_risks), 30.0)
        score -= penalty

        score = max(0.0, min(100.0, score))

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"rug_risk": score},
            evidence={
                "lp_locked_pct": lp_locked_pct,
                "rugcheck_score_normalised": score_normalised,
                "warn_risks": [r.get("name") for r in warn_risks],
                "risk_count": len(risks),
            },
        )

    async def _fetch_summary(self, mint: str) -> dict[str, Any] | None:
        url = f"{RUGCHECK_BASE}/v1/tokens/{mint}/report/summary"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("rugcheck_summary_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            logger.warning(
                "rugcheck_summary_non_200", mint=mint, status=resp.status_code
            )
            return None
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("rugcheck_summary_bad_json", mint=mint, error=str(e))
            return None
