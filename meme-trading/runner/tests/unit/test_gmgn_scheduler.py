"""GMGNScheduler end-to-end: Apify stub → persist → vet → shadow tier."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.curation.gmgn_scheduler import GMGNScheduler
from runner.curation.wallet_vetting import WalletVetter
from runner.db.database import Database


class _FakeWeights:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def get(self, path: str, default=None):
        node = self._cfg
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def check_and_reload(self):
        pass


class _FakeApify:
    """Returns canned candidate lists; records each query made."""
    def __init__(self, items_per_query: list[list[dict]]):
        self._queues = items_per_query
        self.calls: list[dict] = []

    async def discover_copytrade_wallets(self, **kw):
        self.calls.append(kw)
        return self._queues.pop(0) if self._queues else []


class _FakeRanker:
    def score(self, data):
        return {"composite": data.get("composite_score", 80.0)}


class _FakeTierRebuilder:
    """Stubbed Helius verifier — returns whatever we program per wallet."""
    def __init__(self, verdicts: dict[str, dict]):
        self._verdicts = verdicts

    async def verify_single_wallet(self, wallet: str):
        return self._verdicts.get(wallet, {
            "closed_trades": 0, "win_rate": 0.0, "pnl_sol": 0.0,
            "tier": "U", "pairs": [],
        })


def _solid_raw(addr: str, **overrides):
    base = {
        "wallet_address": addr,
        "winrate_7d": 0.68,
        "winrate_30d": 0.60,
        "realized_profit_7d": 9000,
        "realized_profit_30d": 35000,
        "txs_7d": 28,
        "txs_30d": 80,
        "avg_hold_min": 40,
        "largest_trade_pct_of_pnl": 0.30,
        "unrealized_profit": 1000,
        "first_seen_unix": int((datetime.now(timezone.utc) - timedelta(days=200)).timestamp()),
        "composite_score": 82,
    }
    base.update(overrides)
    return base


def _weights(**overrides):
    cfg = {
        "gmgn_discovery": {
            "enabled": True,
            "interval_hours": 24,
            "cap_new_per_run": 20,
            "gmgn_filters": {
                "min_composite_score": 70.0,
                "min_7d_winrate": 0.55,
                "min_30d_winrate": 0.50,
                "min_7d_pnl_usd": 3000,
                "min_trade_count_30d": 20,
                "max_trade_count_30d": 500,
                "max_avg_hold_minutes": 240,
                "max_largest_trade_pct_of_pnl": 0.70,
                "min_realized_to_unrealized_ratio": 0.50,
                "require_age_days": 30,
            },
            "helius_verify": {
                "min_closed_trades": 15,
                "min_winrate": 0.45,
                "min_pnl_sol": 20,
                "required_tier": "B",
            },
            "behavioral": {
                "max_single_token_pct": 0.80,
                "max_hf_burst_pct": 0.50,
                "max_top3_pnl_pct": 0.90,
            },
        },
        "wallet_tier": {
            "a_tier_win_rate": 0.60,
            "b_tier_win_rate": 0.35,
            "a_tier_min_trades": 5,
            "rolling_window_days": 30,
        },
    }
    return _FakeWeights(cfg)


@pytest.mark.asyncio
async def test_scheduler_admits_only_fully_vetted_wallets(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()

    from runner.curation.tier_rebuilder import _Pair
    base_t = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # Build diverse pairs for the "solid" wallet to pass Stage 4
    solid_pairs = [
        _Pair(
            mint=f"MINT_{i}", entry_price_sol=0.001, exit_price_sol=0.002,
            entry_sol=1.0, exit_sol=1.5,
            entry_time=base_t + timedelta(hours=i * 3),
            exit_time=base_t + timedelta(hours=i * 3 + 1),
        ) for i in range(15)
    ]

    tier_rebuilder = _FakeTierRebuilder({
        "SOLID1111111111111111111111111111111111": {
            "closed_trades": 25, "win_rate": 0.60, "pnl_sol": 40.0,
            "tier": "A", "pairs": solid_pairs,
        },
        # Fails Helius verify (low winrate)
        "HELIUSFAIL22222222222222222222222222222": {
            "closed_trades": 20, "win_rate": 0.20, "pnl_sol": 5.0,
            "tier": "C", "pairs": [],
        },
    })

    vetter = WalletVetter(db=db, tier_rebuilder=tier_rebuilder, weights=_weights())
    apify = _FakeApify([
        [
            _solid_raw("SOLID1111111111111111111111111111111111"),
            _solid_raw("HELIUSFAIL22222222222222222222222222222"),
            _solid_raw("STAGE2FAIL33333333333333333333333333333", winrate_7d=0.30),
        ],
    ] + [[] for _ in range(4)])

    scheduler = GMGNScheduler(
        db=db, weights=_weights(), vetter=vetter,
        apify_client=apify, ranker=_FakeRanker(),
    )

    stats = await scheduler.discover_once()

    assert stats.scraped == 3
    assert stats.new_raw == 3
    assert stats.vetted == 3
    assert stats.rejected == 2   # Stage 2 fail + Stage 3 fail
    assert stats.shadowed == 1   # SOLID1

    # Verify DB state
    async with db.conn.execute(
        "SELECT wallet_address, stage, stage_reason FROM gmgn_candidates ORDER BY wallet_address"
    ) as cur:
        rows = [(w, s, r) async for (w, s, r) in cur]
    stages = {w: (s, r) for (w, s, r) in rows}
    assert stages["SOLID1111111111111111111111111111111111"][0] == "shadow"
    assert stages["HELIUSFAIL22222222222222222222222222222"][0] == "rejected"
    assert "stage3:" in stages["HELIUSFAIL22222222222222222222222222222"][1]
    assert stages["STAGE2FAIL33333333333333333333333333333"][0] == "rejected"
    assert "stage2:" in stages["STAGE2FAIL33333333333333333333333333333"][1]

    # Shadow tier row was written only for the solid wallet
    async with db.conn.execute(
        "SELECT wallet_address, tier, source, source_stage FROM wallet_tiers"
    ) as cur:
        tiers = {w: (t, s, ss) async for (w, t, s, ss) in cur}
    assert tiers["SOLID1111111111111111111111111111111111"] == ("S", "gmgn-live", "shadow")
    assert "HELIUSFAIL22222222222222222222222222222" not in tiers
    assert "STAGE2FAIL33333333333333333333333333333" not in tiers

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_skips_existing_active_wallets(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()

    # Seed an already-A wallet
    await db.conn.execute(
        "INSERT INTO wallet_tiers (wallet_address, tier, source) VALUES (?, 'A', 'manual')",
        ("EXISTING_A_WALLET_111111111111111111111",),
    )
    await db.conn.commit()

    tier_rebuilder = _FakeTierRebuilder({})
    vetter = WalletVetter(db=db, tier_rebuilder=tier_rebuilder, weights=_weights())
    apify = _FakeApify([
        [_solid_raw("EXISTING_A_WALLET_111111111111111111111")],
    ] + [[] for _ in range(4)])

    scheduler = GMGNScheduler(
        db=db, weights=_weights(), vetter=vetter,
        apify_client=apify, ranker=_FakeRanker(),
    )
    stats = await scheduler.discover_once()

    assert stats.scraped == 1
    assert stats.new_raw == 0   # skipped because already active
    assert stats.vetted == 0

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_respects_cap_new_per_run(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()

    tier_rebuilder = _FakeTierRebuilder({})
    w = _weights()
    # Lower the cap
    w._cfg["gmgn_discovery"]["cap_new_per_run"] = 2

    vetter = WalletVetter(db=db, tier_rebuilder=tier_rebuilder, weights=w)
    apify = _FakeApify([
        [_solid_raw(f"WALLET_{i}_{'X' * 36}") for i in range(10)],
    ] + [[] for _ in range(4)])

    scheduler = GMGNScheduler(
        db=db, weights=w, vetter=vetter,
        apify_client=apify, ranker=_FakeRanker(),
    )
    stats = await scheduler.discover_once()

    assert stats.new_raw == 2  # cap enforced

    await db.close()
