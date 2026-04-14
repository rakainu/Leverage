"""OutcomeTracker — DexScreener polling, peak mcap recording, moonshot alerts."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.db.database import Database
from runner.outcomes.tracker import OutcomeTracker
from runner.utils.http import RateLimitedClient


def _ds_pair(mint: str, price_usd: float, fdv: float, liq_usd: float = 50_000.0) -> dict:
    return {
        "baseToken": {"address": mint, "symbol": "TEST"},
        "priceUsd": str(price_usd),
        "fdv": fdv,
        "marketCap": fdv,
        "liquidity": {"usd": liq_usd},
        "pairAddress": f"PAIR_{mint[:6]}",
        "dexId": "raydium",
    }


async def _seed_score(db: Database, mint: str, verdict: str, score: float):
    assert db.conn is not None
    await db.conn.execute(
        """INSERT INTO runner_scores
           (token_mint, runner_score, verdict, sub_scores_json, explanation_json)
           VALUES (?, ?, ?, '{}', '{}')""",
        (mint, score, verdict),
    )
    await db.conn.commit()


@pytest.mark.asyncio
async def test_records_peak_and_alerts_on_filter_miss(tmp_path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    await db.connect()

    # Two mints — one we ignored (the "miss"), one we scored watch.
    await _seed_score(db, "MissMint11111111111111111111111111111111111", "ignore", 0.0)
    await _seed_score(db, "WatchMint1111111111111111111111111111111111", "watch", 55.0)

    client = RateLimitedClient(default_rps=100)
    bus: asyncio.Queue = asyncio.Queue()
    tracker = OutcomeTracker(
        db=db, http=client, alert_bus=bus,
        poll_interval_sec=999, moonshot_mcap_usd=1_000_000,
    )

    # First poll: both tokens at $200k — neither moons yet
    body_low = {
        "pairs": [
            _ds_pair("MissMint11111111111111111111111111111111111", 0.0002, 200_000),
            _ds_pair("WatchMint1111111111111111111111111111111111", 0.001, 200_000),
        ]
    }
    # Second poll: ignored mint pumps to $1.5M FDV → triggers filter-miss alert
    body_moon = {
        "pairs": [
            _ds_pair("MissMint11111111111111111111111111111111111", 0.0015, 1_500_000),
            _ds_pair("WatchMint1111111111111111111111111111111111", 0.001, 200_000),
        ]
    }

    with respx.mock(base_url="https://api.dexscreener.com") as mock:
        route = mock.get(path__startswith="/latest/dex/tokens/")
        route.side_effect = [
            httpx.Response(200, json=body_low),
            httpx.Response(200, json=body_moon),
        ]
        await tracker.poll_once()
        await tracker.poll_once()

    # Verify peak recorded
    async with db.conn.execute(
        "SELECT entry_mcap_usd, peak_mcap_usd, moonshot_alerted "
        "FROM token_outcomes WHERE token_mint = ?",
        ("MissMint11111111111111111111111111111111111",),
    ) as cur:
        miss_row = await cur.fetchone()
    assert miss_row[0] == pytest.approx(200_000)
    assert miss_row[1] == pytest.approx(1_500_000)
    assert miss_row[2] == 1

    # Verify exactly one moonshot alert was enqueued, and it's tagged filter_miss
    assert bus.qsize() == 1
    alert = await bus.get()
    assert alert["type"] == "moonshot"
    assert alert["kind"] == "filter_miss"
    assert alert["best_verdict"] == "ignore"
    assert alert["peak_mcap_usd"] == pytest.approx(1_500_000)

    await client.aclose()
    await db.close()


@pytest.mark.asyncio
async def test_alert_fires_only_once_per_mint(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await _seed_score(db, "RepeatMint11111111111111111111111111111111", "ignore", 0.0)

    client = RateLimitedClient(default_rps=100)
    bus: asyncio.Queue = asyncio.Queue()
    tracker = OutcomeTracker(db=db, http=client, alert_bus=bus, poll_interval_sec=999)

    body = {"pairs": [_ds_pair("RepeatMint11111111111111111111111111111111", 0.002, 2_000_000)]}

    with respx.mock(base_url="https://api.dexscreener.com") as mock:
        mock.get(path__startswith="/latest/dex/tokens/").mock(
            return_value=httpx.Response(200, json=body)
        )
        await tracker.poll_once()
        await tracker.poll_once()
        await tracker.poll_once()

    # Only the first crossing produces an alert
    assert bus.qsize() == 1
    await client.aclose()
    await db.close()


@pytest.mark.asyncio
async def test_handles_missing_pair_gracefully(tmp_path):
    """DexScreener returns no pair for a mint → row updated with last_error, no crash."""
    db = Database(tmp_path / "test.db")
    await db.connect()
    await _seed_score(db, "GhostMint11111111111111111111111111111111111", "ignore", 0.0)

    client = RateLimitedClient(default_rps=100)
    bus: asyncio.Queue = asyncio.Queue()
    tracker = OutcomeTracker(db=db, http=client, alert_bus=bus, poll_interval_sec=999)

    with respx.mock(base_url="https://api.dexscreener.com") as mock:
        mock.get(path__startswith="/latest/dex/tokens/").mock(
            return_value=httpx.Response(200, json={"pairs": []})
        )
        result = await tracker.poll_once()

    assert result["checked"] == 1
    assert bus.qsize() == 0

    async with db.conn.execute(
        "SELECT last_error, peak_mcap_usd FROM token_outcomes WHERE token_mint = ?",
        ("GhostMint11111111111111111111111111111111111",),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "no_pair_found"
    assert row[1] is None

    await client.aclose()
    await db.close()
