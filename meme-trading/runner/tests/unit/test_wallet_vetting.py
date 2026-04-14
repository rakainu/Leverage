"""WalletVetter — Stages 2 (GMGN filters), 3 (Helius verify), 4 (behavioral)."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.curation.tier_rebuilder import _Pair
from runner.curation.wallet_vetting import (
    stage2_gmgn_filters,
    stage4_behavioral,
)


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


def _gmgn_weights(**overrides):
    defaults = {
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
    }
    defaults.update(overrides)
    return _FakeWeights({"gmgn_discovery": {"gmgn_filters": defaults}})


def _solid_wallet(**overrides):
    base = {
        "winrate_7d": 0.70,
        "winrate_30d": 0.62,
        "realized_profit_7d": 12000,
        "realized_profit_30d": 45000,
        "txs_7d": 25,
        "txs_30d": 85,
        "avg_hold_min": 45,
        "largest_trade_pct_of_pnl": 0.35,
        "unrealized_profit": 2000,
        "first_seen_unix": int((datetime.now(timezone.utc) - timedelta(days=120)).timestamp()),
        "composite_score": 82,
    }
    base.update(overrides)
    return base


# ── Stage 2 ─────────────────────────────────────────────────────────────


def test_stage2_passes_solid_wallet():
    r = stage2_gmgn_filters(_solid_wallet(), _gmgn_weights())
    assert r.passed, r.reason


def test_stage2_rejects_low_7d_winrate():
    r = stage2_gmgn_filters(_solid_wallet(winrate_7d=0.40), _gmgn_weights())
    assert not r.passed
    assert "low_winrate_7d" in r.reason


def test_stage2_rejects_low_30d_winrate():
    r = stage2_gmgn_filters(_solid_wallet(winrate_30d=0.30), _gmgn_weights())
    assert not r.passed
    assert "low_winrate_30d" in r.reason


def test_stage2_rejects_low_profit():
    r = stage2_gmgn_filters(_solid_wallet(realized_profit_7d=500), _gmgn_weights())
    assert not r.passed
    assert "low_profit_7d" in r.reason


def test_stage2_rejects_one_hit_wonder():
    r = stage2_gmgn_filters(_solid_wallet(txs_30d=5), _gmgn_weights())
    assert not r.passed
    assert "low_activity_30d" in r.reason


def test_stage2_rejects_bot_like_frequency():
    r = stage2_gmgn_filters(_solid_wallet(txs_30d=2000), _gmgn_weights())
    assert not r.passed
    assert "too_active_30d" in r.reason


def test_stage2_rejects_long_hold_swing_trader():
    r = stage2_gmgn_filters(_solid_wallet(avg_hold_min=500), _gmgn_weights())
    assert not r.passed
    assert "long_hold" in r.reason


def test_stage2_rejects_lottery_ticket():
    r = stage2_gmgn_filters(_solid_wallet(largest_trade_pct_of_pnl=0.85), _gmgn_weights())
    assert not r.passed
    assert "lottery_ticket" in r.reason


def test_stage2_rejects_mostly_unrealized():
    r = stage2_gmgn_filters(
        _solid_wallet(realized_profit_30d=1000, unrealized_profit=50000),
        _gmgn_weights(),
    )
    assert not r.passed
    assert "mostly_unrealized" in r.reason


def test_stage2_rejects_young_wallet():
    r = stage2_gmgn_filters(
        _solid_wallet(first_seen_unix=int(datetime.now(timezone.utc).timestamp()) - 5 * 86400),
        _gmgn_weights(),
    )
    assert not r.passed
    assert "too_young" in r.reason


def test_stage2_rejects_low_composite_score():
    r = stage2_gmgn_filters(_solid_wallet(composite_score=50), _gmgn_weights())
    assert not r.passed
    assert "low_composite" in r.reason


# ── Stage 4 ─────────────────────────────────────────────────────────────


def _pair(mint: str, pnl_sol: float, t: datetime) -> _Pair:
    return _Pair(
        mint=mint,
        entry_price_sol=0.001,
        exit_price_sol=0.002,
        entry_sol=1.0,
        exit_sol=1.0 + pnl_sol,
        entry_time=t,
        exit_time=t + timedelta(minutes=30),
    )


def _beh_weights(**overrides):
    defaults = {
        "max_single_token_pct": 0.80,
        "max_hf_burst_pct": 0.50,
        "max_top3_pnl_pct": 0.90,
    }
    defaults.update(overrides)
    return _FakeWeights({"gmgn_discovery": {"behavioral": defaults}})


def test_stage4_passes_diverse_trader():
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # 10 distinct tokens, non-bursty timing, diverse PnL distribution
    pairs = [
        _pair(f"MINT_{i}", 1.0 + i * 0.5, base + timedelta(hours=i * 3))
        for i in range(10)
    ]
    r = stage4_behavioral(pairs, _beh_weights())
    assert r.passed, r.reason


def test_stage4_rejects_single_token_concentration():
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # 9/10 trades on same mint
    pairs = [
        _pair("HOT_MINT", 1.0, base + timedelta(hours=i)) for i in range(9)
    ] + [_pair("OTHER", 1.0, base + timedelta(hours=10))]
    r = stage4_behavioral(pairs, _beh_weights())
    assert not r.passed
    assert "single_token_concentration" in r.reason


def test_stage4_rejects_hf_burst():
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # 8 trades all 30s apart → 7 out of 7 intervals are <60s → 100% burst
    pairs = [
        _pair(f"MINT_{i}", 1.0, base + timedelta(seconds=i * 30))
        for i in range(8)
    ]
    r = stage4_behavioral(pairs, _beh_weights())
    assert not r.passed
    assert "hf_burst" in r.reason


def test_stage4_rejects_top3_concentration():
    base = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    # 3 mints with huge gains, 7 with tiny — top3 dominates PnL
    pairs = [
        _pair("BIG_A", 50.0, base),
        _pair("BIG_B", 40.0, base + timedelta(hours=2)),
        _pair("BIG_C", 30.0, base + timedelta(hours=4)),
    ] + [
        _pair(f"SMALL_{i}", 0.1, base + timedelta(hours=6 + i))
        for i in range(7)
    ]
    r = stage4_behavioral(pairs, _beh_weights())
    assert not r.passed
    assert "top3_pnl_concentration" in r.reason
