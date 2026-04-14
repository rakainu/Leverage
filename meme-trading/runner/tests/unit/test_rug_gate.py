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
async def test_hard_fails_when_rugcheck_unavailable_after_retry(monkeypatch):
    """Fail closed when rugcheck has no data — protects capital from un-vetted tokens."""
    # max_retries=0 skips the 5xx retry loop so this test runs fast
    client = RateLimitedClient(default_rps=100, max_retries=0)
    gate = RugGate(client, lp_locked_pct_min=85)

    # Avoid the real 5s retry sleep
    async def _instant(_):
        return None
    monkeypatch.setattr("runner.filters.rug_gate.asyncio.sleep", _instant)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(400, json={"error": "unable to generate report"}))

        result = await gate.apply(_enriched())

    assert result.passed is False
    assert result.hard_fail_reason == "rugcheck_unavailable_after_retry"
    assert result.evidence["retry_attempted"] is True
    assert "rugcheck_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()


@pytest.mark.asyncio
async def test_passes_when_retry_succeeds(monkeypatch):
    """Retry absorbs indexer lag — first call 400, second call 200 → pass."""
    client = RateLimitedClient(default_rps=100, max_retries=0)
    gate = RugGate(client, lp_locked_pct_min=85)

    async def _instant(_):
        return None
    monkeypatch.setattr("runner.filters.rug_gate.asyncio.sleep", _instant)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        route = mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        )
        route.side_effect = [
            httpx.Response(400, json={"error": "unable to generate report"}),
            httpx.Response(200, json=payload),
        ]

        result = await gate.apply(_enriched())

    assert result.passed is True
    assert result.hard_fail_reason is None
    await client.aclose()
