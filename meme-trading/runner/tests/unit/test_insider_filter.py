"""InsiderFilter — RugCheck /insiders/graph insider count → insider_risk sub-score."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.insider_filter import InsiderFilter
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "rugcheck_insiders_graph.json"


def _enriched(mint="M") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


@pytest.mark.asyncio
async def test_scores_full_when_no_insiders():
    client = RateLimitedClient(default_rps=100)
    filt = InsiderFilter(client)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(200, json={"nodes": [], "edges": []})
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(100.0)
    assert result.evidence["insider_count"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_penalizes_four_insiders():
    """4 insiders lands in the 3-5 band: -15 → 85."""
    client = RateLimitedClient(default_rps=100)
    filt = InsiderFilter(client)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(85.0)
    assert result.evidence["insider_count"] == 4
    await client.aclose()


@pytest.mark.asyncio
async def test_api_failure_returns_zero_subscore():
    client = RateLimitedClient(default_rps=100, max_retries=0)
    filt = InsiderFilter(client)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(500, json={})
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(0.0)
    assert "insiders_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
