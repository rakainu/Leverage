"""Tests for CurationPipeline."""
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from config.settings import Settings
from curation.pipeline import CurationPipeline


@pytest.fixture
def tmp_wallets(tmp_path):
    path = tmp_path / "wallets.json"
    path.write_text(json.dumps({
        "wallets": [
            {"address": "MANUAL1", "label": "manual-one", "source": "manual",
             "score": 80, "active": True, "added_at": "2026-01-01T00:00:00+00:00"},
        ],
        "updated_at": "2026-01-01T00:00:00+00:00",
        "version": 1,
    }))
    return path


@pytest.fixture
def settings(tmp_wallets):
    s = Settings()
    s.wallets_json_path = str(tmp_wallets)
    return s


@pytest.mark.asyncio
async def test_merge_preserves_provided_source(settings, tmp_wallets):
    """A new candidate carrying source=gmgn-apify must land with that source, not 'nansen-live'."""
    pipeline = CurationPipeline(settings)
    new = [{
        "address": "GMGN1",
        "score": 75,
        "stats": {"total_trades": 12, "win_rate": 60, "total_pnl_sol": 0, "avg_hold_minutes": 0},
        "label_hint": "gmgn-75-wr60-$5k7d",
        "source": "gmgn-apify",
    }]
    added, updated, deactivated = await pipeline._merge_wallets(new)
    assert added == 1
    data = json.loads(tmp_wallets.read_text())
    gmgn = next(w for w in data["wallets"] if w["address"] == "GMGN1")
    assert gmgn["source"] == "gmgn-apify"


@pytest.mark.asyncio
async def test_merge_default_source_when_missing(settings, tmp_wallets):
    """If a candidate has no source field, fall back to 'auto'."""
    pipeline = CurationPipeline(settings)
    new = [{
        "address": "ANON1",
        "score": 65,
        "stats": {"total_trades": 5, "win_rate": 50, "total_pnl_sol": 0, "avg_hold_minutes": 0},
        "label_hint": "anon",
    }]
    await pipeline._merge_wallets(new)
    data = json.loads(tmp_wallets.read_text())
    anon = next(w for w in data["wallets"] if w["address"] == "ANON1")
    assert anon["source"] == "auto"


@pytest.mark.asyncio
async def test_discover_gmgn_apify_no_token_returns_empty(settings):
    settings.apify_api_token = ""
    pipeline = CurationPipeline(settings)
    result = await pipeline._discover_gmgn_apify()
    assert result == []


@pytest.mark.asyncio
async def test_discover_gmgn_apify_filters_by_score(settings):
    """Candidates failing GMGNRanker.meets_minimum must be excluded."""
    settings.apify_api_token = "test-token"
    settings.gmgn_min_score = 70.0
    settings.gmgn_max_per_actor = 50
    settings.gmgn_max_new_per_cycle = 20

    # Build a high-score and a low-score candidate from Apify
    now = time.time()
    high = {
        "wallet_address": "HIGH1",
        "winrate_7d": 0.70, "realized_profit_7d": 50000,
        "winrate_30d": 0.65, "realized_profit_30d": 200000,
        "txs_7d": 50, "last_active": now - 3600,
        "pnl_2x_5x_num_7d": 3, "pnl_gt_5x_num_7d": 1,
    }
    low = {
        "wallet_address": "LOW1",
        "winrate_7d": 0.40, "realized_profit_7d": -1000,
        "winrate_30d": 0.45, "realized_profit_30d": -2000,
        "txs_7d": 3, "last_active": now - 3600,
    }

    fake_apify = AsyncMock()
    fake_apify.discover_copytrade_wallets = AsyncMock(side_effect=[[high, low], [high]])

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.ApifyGMGNClient", return_value=fake_apify):
        result = await pipeline._discover_gmgn_apify()

    addrs = [w["address"] for w in result]
    assert "HIGH1" in addrs
    assert "LOW1" not in addrs
    # Source is correctly tagged
    assert all(w["source"] == "gmgn-apify" for w in result)
    # Dedupe collapses HIGH1's two-bucket appearance into one; LOW1 is filtered by score
    assert len(result) == 1


@pytest.mark.asyncio
async def test_discover_gmgn_apify_caps_new_per_cycle(settings):
    """Even with many qualified candidates, cap new additions per cycle."""
    settings.apify_api_token = "test-token"
    settings.gmgn_min_score = 60.0
    settings.gmgn_max_new_per_cycle = 3

    now = time.time()
    candidates = [
        {
            "wallet_address": f"WALLET{i}",
            "winrate_7d": 0.65, "realized_profit_7d": 5000 + i * 100,
            "winrate_30d": 0.60, "realized_profit_30d": 20000,
            "txs_7d": 20, "last_active": now - 3600,
            "pnl_2x_5x_num_7d": 2,
        }
        for i in range(10)
    ]

    fake_apify = AsyncMock()
    fake_apify.discover_copytrade_wallets = AsyncMock(return_value=candidates)

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.ApifyGMGNClient", return_value=fake_apify):
        result = await pipeline._discover_gmgn_apify()

    assert len(result) == 3


@pytest.mark.asyncio
async def test_discover_gmgn_apify_one_bucket_fails_other_succeeds(settings):
    """If one Apify bucket raises, the other's wallets still flow through."""
    settings.apify_api_token = "test-token"
    settings.gmgn_min_score = 60.0
    settings.gmgn_max_new_per_cycle = 20

    now = time.time()
    good = {
        "wallet_address": "GOOD1",
        "winrate_7d": 0.65, "realized_profit_7d": 8000,
        "winrate_30d": 0.60, "realized_profit_30d": 30000,
        "txs_7d": 25, "last_active": now - 3600,
        "pnl_2x_5x_num_7d": 2,
    }

    async def first_bucket_fails(*args, **kwargs):
        if kwargs.get("trader_type") == "smart_degen":
            raise RuntimeError("apify smart_degen blew up")
        return [good]

    fake_apify = AsyncMock()
    fake_apify.discover_copytrade_wallets = AsyncMock(side_effect=first_bucket_fails)

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.ApifyGMGNClient", return_value=fake_apify):
        result = await pipeline._discover_gmgn_apify()

    addrs = [w["address"] for w in result]
    assert addrs == ["GOOD1"]


@pytest.mark.asyncio
async def test_prune_dead_wallets_skips_manual_and_inactive(settings, tmp_wallets, tmp_path):
    """Only auto-source, currently-active wallets with 0 buys in window get deactivated."""
    # Replace wallets file with a richer fixture
    tmp_wallets.write_text(json.dumps({
        "wallets": [
            {"address": "MANUAL1", "label": "m", "source": "manual", "active": True, "score": 80,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "GMGN_ACTIVE", "label": "g1", "source": "gmgn-apify", "active": True, "score": 75,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "GMGN_DEAD", "label": "g2", "source": "gmgn-apify", "active": True, "score": 65,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "ALREADY_OFF", "label": "off", "source": "nansen-live", "active": False, "score": 30,
             "added_at": "2026-01-01T00:00:00+00:00"},
        ],
        "updated_at": "2026-01-01T00:00:00+00:00",
        "version": 1,
    }))

    # Mock the DB query result — only GMGN_DEAD comes back as dead (had no recent buys)
    dead_rows = [{"address": "GMGN_DEAD"}]
    fake_db = AsyncMock()
    fake_db.execute_fetchall = AsyncMock(return_value=dead_rows)
    fake_db.execute = AsyncMock()
    fake_db.commit = AsyncMock()

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.get_db", AsyncMock(return_value=fake_db)):
        pruned = await pipeline._prune_dead_wallets(days=7)

    assert pruned == 1

    data = json.loads(tmp_wallets.read_text())
    by_addr = {w["address"]: w for w in data["wallets"]}
    assert by_addr["MANUAL1"]["active"] is True
    assert by_addr["GMGN_ACTIVE"]["active"] is True
    assert by_addr["GMGN_DEAD"]["active"] is False
    assert by_addr["ALREADY_OFF"]["active"] is False  # unchanged
