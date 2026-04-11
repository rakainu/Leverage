"""HolderFilter — Helius DAS getTokenAccounts with top-10 concentration gate."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.holder_filter import HolderFilter
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "das_getTokenAccounts.json"


def _enriched(deployer: str | None = "DeployerWallet") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
        mint_authority=None,
        freeze_authority=None,
        deployer_address=deployer,
    )


@pytest.mark.asyncio
async def test_hard_fails_when_top10_over_70_pct():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched())

    # Top-10 excluding deployer = 98% — hard fail
    assert result.passed is False
    assert "top" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_passes_with_good_holder_distribution():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    # Distributed supply — no single holder > 10%, top-10 sums to < 70%
    payload = {
        "jsonrpc": "2.0",
        "result": {
            "total": 60,
            "token_accounts": [
                # Top 10 (60% of supply total)
                {"address": f"A{i}", "mint": "M", "owner": f"Holder{i}",
                 "amount": 6_000_000_000, "frozen": False}
                for i in range(10)
            ] + [
                # 50 smaller holders, 40% of supply split
                {"address": f"B{i}", "mint": "M", "owner": f"Small{i}",
                 "amount": 800_000_000, "frozen": False}
                for i in range(50)
            ]
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched(deployer=None))

    assert result.passed is True
    assert result.sub_scores["holder_quality"] > 0
    assert result.evidence["unique_holders"] == 60
    assert result.evidence["top10_pct"] == pytest.approx(60.0, abs=0.5)
    await client.aclose()


@pytest.mark.asyncio
async def test_holder_quality_score_scales_with_count_and_concentration():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    # 100 holders, top-10 = 35% — should score well
    payload = {
        "jsonrpc": "2.0",
        "result": {
            "total": 100,
            "token_accounts": [
                {"address": f"A{i}", "mint": "M", "owner": f"H{i}",
                 "amount": 3_500_000_000, "frozen": False}
                for i in range(10)
            ] + [
                {"address": f"B{i}", "mint": "M", "owner": f"S{i}",
                 "amount": 722_222_222, "frozen": False}
                for i in range(90)
            ]
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched(deployer=None))

    assert result.passed is True
    # With > 100 holders AND top-10 30-45% concentration → ~50 points
    assert 40 <= result.sub_scores["holder_quality"] <= 80
    await client.aclose()


@pytest.mark.asyncio
async def test_api_failure_returns_pass_with_zero_subscore():
    client = RateLimitedClient(default_rps=100, max_retries=0)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(500, json={}))
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["holder_quality"] == 0.0
    assert "das_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
