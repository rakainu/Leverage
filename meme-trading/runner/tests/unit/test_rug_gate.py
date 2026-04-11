"""RugGate filter — RugCheck /report/summary-based hard gates + rug_risk sub-score."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.rug_gate import RugGate
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "rugcheck_report_summary.json"


def _enriched(mint="TestMint1111111111111111111111111111111111") -> EnrichedToken:
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
        mint_authority=None,
        freeze_authority=None,
    )


@pytest.mark.asyncio
async def test_passes_when_lp_locked_and_no_hard_risks():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        result = await gate.apply(_enriched())

    assert result.filter_name == "rug_gate"
    assert result.passed is True
    assert result.hard_fail_reason is None
    # score_normalised=12, one warn risk (-5) → 100 - 12 - 5 = 83
    assert result.sub_scores["rug_risk"] == pytest.approx(83, abs=1)
    assert result.evidence["lp_locked_pct"] == pytest.approx(95.0)
    await client.aclose()


@pytest.mark.asyncio
async def test_hard_fails_when_lp_locked_below_threshold():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())
    payload["lpLockedPct"] = 40.0  # below threshold

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        result = await gate.apply(_enriched())

    assert result.passed is False
    assert "lp" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_hard_fails_when_mint_authority_still_enabled():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())

    with respx.mock(
        base_url="https://api.rugcheck.xyz", assert_all_called=False
    ) as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        # EnrichedToken with non-None mint_authority → hard fail on that check
        base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
        sig = ClusterSignal(
            token_mint="TestMint1111111111111111111111111111111111",
            wallets=["A1", "A2", "B1"],
            wallet_count=3,
            tier_counts={"A": 2, "B": 1},
            first_buy_time=base,
            last_buy_time=base + timedelta(minutes=10),
            convergence_seconds=600,
            mid_price_sol=0.00025,
        )
        enriched = EnrichedToken(
            token_mint="TestMint1111111111111111111111111111111111",
            cluster_signal=sig,
            enriched_at=base + timedelta(minutes=11),
            mint_authority="SomeAuth111111111111111111111111111111",  # NOT revoked
            freeze_authority=None,
        )

        result = await gate.apply(enriched)

    assert result.passed is False
    assert "mint" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_passes_with_zero_score_when_rugcheck_fails():
    """API failure should not hard-fail; gate degrades to low sub-score but passes."""
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(500, json={}))

        result = await gate.apply(_enriched())

    # API failure shouldn't hard-fail — operator decides upstream. Mark as
    # degraded and let the rug_risk sub-score go to 0.
    assert result.passed is True
    assert result.sub_scores["rug_risk"] == pytest.approx(0, abs=1)
    assert "rugcheck_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
