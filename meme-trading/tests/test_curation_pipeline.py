"""Tests for CurationPipeline."""
import json
from pathlib import Path

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
