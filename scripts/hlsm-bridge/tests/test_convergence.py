"""Convergence detector unit tests. Pure logic; no DB or network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from hlsm.convergence import ConvergenceDetector
from hlsm.convergence.detector import DetectorConfig
from hlsm.convergence.events import WalletOpenEvent
from hlsm.exchange.types import Side


def _ev(addr: str, score: float, ts: datetime, coin: str = "PEPE", side: Side = Side.LONG) -> WalletOpenEvent:
    return WalletOpenEvent(wallet_address=addr, score=score, coin=coin, side=side, ts=ts)


def test_fires_when_three_wallets_aligned_in_window():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    assert d.on_open(_ev("0xaaa", 90, t0)) is None
    assert d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=10))) is None
    fired = d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=20)))

    assert fired is not None
    assert fired.coin == "PEPE"
    assert fired.side == Side.LONG
    assert fired.wallet_count == 3
    assert set(fired.wallet_addresses) == {"0xaaa", "0xbbb", "0xccc"}
    assert fired.score_floor_used == 75
    assert fired.window_seconds == 45 * 60


def test_does_not_fire_outside_window():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    d.on_open(_ev("0xaaa", 90, t0))
    d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=10)))
    # third arrives outside the 45min window from the first — the first should drop out
    result = d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=50)))
    assert result is None


def test_score_floor_excludes_low_quality_wallets():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    d.on_open(_ev("0xaaa", 90, t0))
    d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=5)))
    # 3rd wallet has score 60 (below floor 75) - must not count
    assert d.on_open(_ev("0xddd", 60, t0 + timedelta(minutes=10))) is None


def test_universe_filter_blocks_off_list_coins():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75,
                        universe=frozenset({"PEPE", "WIF"}))
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    # 3 wallets on BTC (not in universe) — should never fire
    d.on_open(_ev("0xaaa", 90, t0, coin="BTC"))
    d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=1), coin="BTC"))
    fired = d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=2), coin="BTC"))
    assert fired is None


def test_same_wallet_set_does_not_refire_within_cooldown():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75, cooldown_minutes=60)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    d.on_open(_ev("0xaaa", 90, t0))
    d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=5)))
    first = d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=10)))
    assert first is not None

    # Same wallets re-open shortly after — should NOT re-fire (dedup)
    again = d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=15)))
    assert again is None


def test_long_and_short_are_separate_clusters():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    # 2 long + 2 short on PEPE — neither side has 3, nothing should fire
    d.on_open(_ev("0xaaa", 90, t0, side=Side.LONG))
    d.on_open(_ev("0xbbb", 85, t0 + timedelta(minutes=1), side=Side.LONG))
    d.on_open(_ev("0xccc", 80, t0 + timedelta(minutes=2), side=Side.SHORT))
    fired = d.on_open(_ev("0xddd", 80, t0 + timedelta(minutes=3), side=Side.SHORT))
    assert fired is None


def test_replay_returns_chronological_events():
    cfg = DetectorConfig(cluster_n=3, window_minutes=45, score_floor=75)
    d = ConvergenceDetector(cfg)
    t0 = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)

    events = [
        _ev("0xaaa", 90, t0 + timedelta(minutes=10)),
        _ev("0xbbb", 85, t0),
        _ev("0xccc", 80, t0 + timedelta(minutes=20)),
    ]
    fired = d.replay(events)
    assert len(fired) == 1
    assert fired[0].wallet_count == 3
