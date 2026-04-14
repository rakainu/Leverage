"""TierRebuilder — pair matching, tier classification, schedule logic."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.wallet_registry import WalletRegistry
from runner.cluster.wallet_tier import WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.curation.tier_rebuilder import TierRebuilder, _Pair, _TradeLeg
from runner.db.database import Database
from runner.utils.http import RateLimitedClient


W1 = "Wal11111111111111111111111111111111111111111"
W2 = "Wal22222222222222222222222222222222222222222"
M1 = "Mint1111111111111111111111111111111111111111"
M2 = "Mint2222222222222222222222222222222222222222"


def _write_wallets(tmp_path: Path, addresses: list[str]) -> Path:
    p = tmp_path / "wallets.json"
    p.write_text(json.dumps({
        "wallets": [{"address": a, "name": a, "active": True} for a in addresses]
    }))
    return p


def _write_weights(tmp_path: Path) -> Path:
    p = tmp_path / "weights.yaml"
    p.write_text(
        "wallet_tier:\n"
        "  a_tier_win_rate: 0.60\n"
        "  a_tier_min_trades: 5\n"
        "  b_tier_win_rate: 0.35\n"
        "  rebuild_hour_utc: 4\n"
        "  rolling_window_days: 30\n"
    )
    return p


async def _bootstrap_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.connect()
    return db


@pytest.mark.asyncio
async def test_tier_assignment_a_tier_threshold(tmp_path):
    db = await _bootstrap_db(tmp_path)
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1]))
    registry.load()
    cache = WalletTierCache(db); await cache.load()
    client = RateLimitedClient(default_rps=100)

    rb = TierRebuilder(
        db=db, http=client, registry=registry, weights=weights,
        helius_rpc_url="https://x", tier_cache=cache, run_on_startup=False,
    )
    base = datetime.now(timezone.utc) - timedelta(days=1)
    # 4 wins + 1 loss = 80% wr, 5 trades → tier A
    pairs = [
        _Pair(M1, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=1)),  # win
        _Pair(M1, 0.001, 0.003, 0.5, 1.5, base, base + timedelta(hours=2)),  # win
        _Pair(M1, 0.001, 0.004, 0.5, 2.0, base, base + timedelta(hours=3)),  # win
        _Pair(M1, 0.001, 0.005, 0.5, 2.5, base, base + timedelta(hours=4)),  # win
        _Pair(M1, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=5)),  # loss
    ]
    promos, demos = await rb._retier({W1: pairs})

    async with db.conn.execute(
        "SELECT tier, win_rate, trade_count, source FROM wallet_tiers WHERE wallet_address=?",
        (W1,),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "A"
    assert row[1] == pytest.approx(0.8)
    assert row[2] == 5
    assert row[3] == "tier_rebuilder"
    await client.aclose(); await db.close()


@pytest.mark.asyncio
async def test_tier_b_and_c_thresholds(tmp_path):
    db = await _bootstrap_db(tmp_path)
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1, W2]))
    registry.load()
    cache = WalletTierCache(db); await cache.load()
    client = RateLimitedClient(default_rps=100)
    rb = TierRebuilder(
        db=db, http=client, registry=registry, weights=weights,
        helius_rpc_url="https://x", tier_cache=cache, run_on_startup=False,
    )
    base = datetime.now(timezone.utc) - timedelta(days=1)
    # W1: 2 wins / 5 → 40% → B
    w1_pairs = [
        _Pair(M1, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=1)),
        _Pair(M1, 0.001, 0.003, 0.5, 1.5, base, base + timedelta(hours=2)),
        _Pair(M1, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=3)),
        _Pair(M1, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=4)),
        _Pair(M1, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=5)),
    ]
    # W2: 1 win / 5 = 20% → C
    w2_pairs = [
        _Pair(M2, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=1)),
        _Pair(M2, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=2)),
        _Pair(M2, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=3)),
        _Pair(M2, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=4)),
        _Pair(M2, 0.002, 0.001, 1.0, 0.5, base, base + timedelta(hours=5)),
    ]
    await rb._retier({W1: w1_pairs, W2: w2_pairs})
    async with db.conn.execute("SELECT wallet_address, tier FROM wallet_tiers") as cur:
        tiers = {r[0]: r[1] async for r in cur}
    assert tiers[W1] == "B"
    assert tiers[W2] == "C"
    await client.aclose(); await db.close()


@pytest.mark.asyncio
async def test_insufficient_trades_stays_unknown(tmp_path):
    db = await _bootstrap_db(tmp_path)
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1]))
    registry.load()
    cache = WalletTierCache(db); await cache.load()
    client = RateLimitedClient(default_rps=100)
    rb = TierRebuilder(
        db=db, http=client, registry=registry, weights=weights,
        helius_rpc_url="https://x", tier_cache=cache, run_on_startup=False,
    )
    base = datetime.now(timezone.utc) - timedelta(days=1)
    # Only 3 trades — below a_tier_min_trades of 5 → tier U
    pairs = [
        _Pair(M1, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=1)),
        _Pair(M1, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=2)),
        _Pair(M1, 0.001, 0.002, 0.5, 1.0, base, base + timedelta(hours=3)),
    ]
    await rb._retier({W1: pairs})
    async with db.conn.execute("SELECT tier FROM wallet_tiers WHERE wallet_address=?", (W1,)) as cur:
        row = await cur.fetchone()
    assert row[0] == "U"
    await client.aclose(); await db.close()


@pytest.mark.asyncio
async def test_persist_pairs_dedupes_on_rerun(tmp_path):
    db = await _bootstrap_db(tmp_path)
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1]))
    registry.load()
    cache = WalletTierCache(db); await cache.load()
    client = RateLimitedClient(default_rps=100)
    rb = TierRebuilder(
        db=db, http=client, registry=registry, weights=weights,
        helius_rpc_url="https://x", tier_cache=cache, run_on_startup=False,
    )
    t = datetime.now(timezone.utc) - timedelta(days=1)
    pairs = [_Pair(M1, 0.001, 0.002, 0.5, 1.0, t, t + timedelta(hours=1))]
    n1 = await rb._persist_pairs(W1, pairs)
    n2 = await rb._persist_pairs(W1, pairs)
    assert n1 == 1
    assert n2 == 0  # dedup on (wallet, mint, entry_time)
    await client.aclose(); await db.close()


@pytest.mark.asyncio
async def test_full_rebuild_with_mocked_helius(tmp_path):
    """End-to-end: mocked Helius signature list + 4 txs → 2 trade pairs → tier B."""
    db = await _bootstrap_db(tmp_path)
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1]))
    registry.load()
    cache = WalletTierCache(db); await cache.load()
    client = RateLimitedClient(default_rps=100)
    rb = TierRebuilder(
        db=db, http=client, registry=registry, weights=weights,
        helius_rpc_url="https://x.helius/v0/", tier_cache=cache, run_on_startup=False,
    )
    now = int(datetime.now(timezone.utc).timestamp())
    # 2 buys + 2 sells of same mint, alternating, won and lost
    sigs = [
        {"signature": "S4", "blockTime": now - 100},
        {"signature": "S3", "blockTime": now - 200},
        {"signature": "S2", "blockTime": now - 300},
        {"signature": "S1", "blockTime": now - 400},
    ]

    def _tx(sig: str, sol_change: float, token_change: float):
        # SOL change via lamports; token change via balance delta on M1
        pre_lamports = 10_000_000_000
        post_lamports = pre_lamports + int(sol_change * 1_000_000_000)
        pre_amt = max(0.0, -token_change)
        post_amt = pre_amt + token_change
        return {
            "result": {
                "meta": {
                    "err": None, "fee": 5000,
                    "preBalances": [pre_lamports], "postBalances": [post_lamports],
                    "preTokenBalances": [{"owner": W1, "mint": M1, "uiTokenAmount": {"uiAmount": pre_amt}}],
                    "postTokenBalances": [{"owner": W1, "mint": M1, "uiTokenAmount": {"uiAmount": post_amt}}],
                },
                "transaction": {"message": {"accountKeys": [{"pubkey": W1}]}},
                "blockTime": now,
            }
        }

    # S1 buy (sol -0.5, token +500), S2 sell (sol +1.0, token -500) → win
    # S3 buy (sol -0.5, token +500), S4 sell (sol +0.25, token -500) → loss
    tx_map = {
        "S1": _tx("S1", -0.5, 500),
        "S2": _tx("S2", 1.0, -500),
        "S3": _tx("S3", -0.5, 500),
        "S4": _tx("S4", 0.25, -500),
    }

    with respx.mock(base_url="https://x.helius") as mock:
        async def handler(request: httpx.Request):
            body = json.loads(request.content)
            method = body.get("method")
            if method == "getSignaturesForAddress":
                return httpx.Response(200, json={"result": sigs})
            if method == "getTransaction":
                sig = body["params"][0]
                return httpx.Response(200, json=tx_map[sig])
            return httpx.Response(404)
        mock.post(path__startswith="/v0/").mock(side_effect=handler)

        stats = await rb.rebuild_now()

    assert stats["wallets_processed"] == 1
    assert stats["wallets_with_trades"] == 1
    # 2 closed pairs → trade_count=2, win_rate=0.5
    async with db.conn.execute(
        "SELECT trade_count, win_rate, tier FROM wallet_tiers WHERE wallet_address=?",
        (W1,),
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 2
    assert row[1] == pytest.approx(0.5)
    # Below a_tier_min_trades (5) → tier U regardless of WR
    assert row[2] == "U"
    await client.aclose(); await db.close()


def test_seconds_until_next_run_handles_past_target(tmp_path):
    weights = WeightsLoader(_write_weights(tmp_path))
    registry = WalletRegistry(_write_wallets(tmp_path, [W1]))
    registry.load()
    rb = TierRebuilder(
        db=None, http=None, registry=registry, weights=weights,
        helius_rpc_url="https://x", tier_cache=None, run_on_startup=False,
    )
    sec = rb._seconds_until_next_run()
    # Always between 60 and 24h+1
    assert 60.0 <= sec <= 86460.0
