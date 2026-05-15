"""Stats calculator + composite scorer tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hlsm.db import HlPosition, Wallet
from hlsm.scoring import ScoringConfig, score_wallet, score_all
from hlsm.stats import WalletStats, compute_wallet_stats


def _make_position(session, *, wallet, coin, pnl, pnl_pct, opened, closed, side="long"):
    session.add(HlPosition(
        wallet_address=wallet, coin=coin, side=side,
        opened_at=opened, closed_at=closed,
        entry_px=Decimal("1"), exit_px=Decimal("1"),
        sz=Decimal("100"),
        realized_pnl=Decimal(str(pnl)),
        realized_pnl_pct=Decimal(str(pnl_pct)),
        hold_seconds=int((closed - opened).total_seconds()),
        status="closed",
    ))


def test_stats_winrate_and_sample_size(session):
    session.add(Wallet(address="0xaaa"))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    for i in range(10):
        pnl = 1.0 if i < 6 else -0.5
        pnl_pct = 5.0 if i < 6 else -3.0
        _make_position(session, wallet="0xaaa", coin="PEPE",
                       pnl=pnl, pnl_pct=pnl_pct,
                       opened=t0 + timedelta(hours=i * 2),
                       closed=t0 + timedelta(hours=i * 2 + 1))
    session.flush()

    stats = compute_wallet_stats(session, "0xaaa")
    assert stats.sample_size == 10
    assert stats.win_rate == 0.6


def test_scoring_anti_fluke_blocks_too_few_trades():
    stats = WalletStats(sample_size=30, sharpe_proxy=1.5, max_dd_pct=10,
                       win_rate=0.55, avg_hold_seconds=1800,
                       last_trade_at=datetime.now(timezone.utc),
                       max_single_trade_pnl_share=0.1)
    sw = score_wallet(stats, config=ScoringConfig())
    assert sw.passes_anti_fluke is False
    assert "sample_size" in (sw.fluke_reason or "")
    assert sw.composite == 0.0


def test_scoring_anti_fluke_blocks_single_trade_dominance():
    stats = WalletStats(sample_size=100, sharpe_proxy=1.5, max_dd_pct=10,
                       win_rate=0.55, avg_hold_seconds=1800,
                       last_trade_at=datetime.now(timezone.utc),
                       max_single_trade_pnl_share=0.6)  # 60% of total |PnL|
    sw = score_wallet(stats, config=ScoringConfig())
    assert sw.passes_anti_fluke is False
    assert "dominates" in (sw.fluke_reason or "")


def test_scoring_composite_in_range_for_good_wallet():
    stats = WalletStats(sample_size=200, sharpe_proxy=2.5, max_dd_pct=8,
                       win_rate=0.62, avg_hold_seconds=900,
                       last_trade_at=datetime.now(timezone.utc),
                       max_single_trade_pnl_share=0.05)
    sw = score_wallet(stats, config=ScoringConfig())
    assert sw.passes_anti_fluke
    assert 50 <= sw.composite <= 100


def test_scoring_low_for_bad_wallet():
    stats = WalletStats(sample_size=80, sharpe_proxy=-1.0, max_dd_pct=45,
                       win_rate=0.3, avg_hold_seconds=10,
                       last_trade_at=datetime.now(timezone.utc) - timedelta(days=180),
                       max_single_trade_pnl_share=0.2)
    sw = score_wallet(stats, config=ScoringConfig())
    assert sw.passes_anti_fluke
    assert sw.composite < 30


def test_score_all_writes_scores_history(session):
    session.add(Wallet(address="0xaaa", active=True))
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    # Seed 60 closed positions, all small wins
    for i in range(60):
        _make_position(session, wallet="0xaaa", coin="PEPE",
                       pnl=0.5, pnl_pct=2.5,
                       opened=t0 + timedelta(hours=i),
                       closed=t0 + timedelta(hours=i, minutes=30))
    session.flush()

    scored = score_all(session, config=ScoringConfig())
    assert len(scored) == 1
    sw = scored[0]
    assert sw.address == "0xaaa"
    assert sw.passes_anti_fluke
    assert sw.composite > 0

    from hlsm.db import ScoreHistory
    rows = session.query(ScoreHistory).filter_by(wallet_address="0xaaa").all()
    assert len(rows) == 1
    assert float(rows[0].composite) == sw.composite
