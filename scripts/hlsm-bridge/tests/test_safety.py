"""Off-switch + circuit breaker tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hlsm.db import PaperPosition, Signal
from hlsm.safety import (
    apply_drain,
    apply_pause,
    apply_pause_coin,
    apply_resume,
    apply_resume_coin,
    gate_entry,
)
from hlsm.safety.circuit_breaker import CircuitBreaker
from hlsm.safety.state import get_safety_state


def test_default_allows_entry(session):
    r = gate_entry(session=session, coin="PEPE",
                   open_paper_count=0, max_concurrent=5)
    assert r.allowed
    assert r.decision.value == "allow"


def test_pause_blocks_entry(session):
    apply_pause(session)
    session.commit()
    r = gate_entry(session=session, coin="PEPE", open_paper_count=0, max_concurrent=5)
    assert not r.allowed
    assert r.decision.value == "skipped_paused"


def test_resume_clears_pause_and_breaker(session):
    apply_pause(session)
    session.commit()
    apply_resume(session)
    session.commit()
    state = get_safety_state(session)
    assert state.paused_global is False
    assert state.drain_mode is False
    assert state.breaker_tripped is False


def test_drain_blocks_entry_even_when_not_paused(session):
    apply_drain(session)
    session.commit()
    r = gate_entry(session=session, coin="PEPE", open_paper_count=0, max_concurrent=5)
    assert not r.allowed
    assert r.decision.value == "skipped_drain"


def test_per_coin_pause_only_blocks_that_coin(session):
    apply_pause_coin(session, "PEPE")
    session.commit()
    blocked = gate_entry(session=session, coin="PEPE", open_paper_count=0, max_concurrent=5)
    allowed = gate_entry(session=session, coin="WIF", open_paper_count=0, max_concurrent=5)
    assert not blocked.allowed
    assert blocked.decision.value == "skipped_coin_paused"
    assert allowed.allowed


def test_per_coin_resume_unblocks(session):
    apply_pause_coin(session, "PEPE")
    session.commit()
    apply_resume_coin(session, "PEPE")
    session.commit()
    r = gate_entry(session=session, coin="PEPE", open_paper_count=0, max_concurrent=5)
    assert r.allowed


def test_max_concurrent_blocks_when_reached(session):
    r = gate_entry(session=session, coin="PEPE", open_paper_count=5, max_concurrent=5)
    assert not r.allowed
    assert r.decision.value == "skipped_max_concurrent"


def test_universe_blocks_off_list_coin(session):
    universe = frozenset({"PEPE", "WIF"})
    r = gate_entry(session=session, coin="BTC", open_paper_count=0, max_concurrent=5, universe=universe)
    assert not r.allowed
    assert r.decision.value == "skipped_universe"


def _seed_closed_position(session, pnl_usdt: Decimal,
                          closed_at: datetime | None = None) -> None:
    closed_at = closed_at or datetime.now(timezone.utc)
    signal = Signal(
        fired_at=closed_at - timedelta(hours=1),
        coin="PEPE", side="long", wallet_count=3,
        wallet_addresses="0xa,0xb,0xc", score_floor_used=Decimal("75"),
        window_seconds=2700, status="filled",
    )
    session.add(signal)
    session.flush()
    pp = PaperPosition(
        signal_id=signal.id, venue="blofin",
        coin="PEPE", side="long",
        margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
        entry_px=Decimal("0.00001"), sl_px=Decimal("0.0000075"), tp_px=Decimal("0.000013"),
        opened_at=closed_at - timedelta(hours=1),
        closed_at=closed_at,
        exit_px=Decimal("0.0000075"),
        realized_pnl_usdt=pnl_usdt,
        realized_pnl_pct=Decimal("-250") if pnl_usdt < 0 else Decimal("250"),
        status="closed",
        close_reason="sl" if pnl_usdt < 0 else "tp",
    )
    session.add(pp)
    session.flush()


def test_circuit_breaker_trips_when_threshold_crossed(session):
    now = datetime.now(timezone.utc)
    _seed_closed_position(session, Decimal("-60"), closed_at=now)
    _seed_closed_position(session, Decimal("-50"), closed_at=now)
    session.commit()

    breaker = CircuitBreaker(threshold_usdt=Decimal("100"))
    tripped = breaker.check(session, as_of=now)
    assert tripped is True
    state = get_safety_state(session)
    assert state.breaker_tripped is True
    assert state.paused_global is True


def test_circuit_breaker_no_trip_above_threshold(session):
    now = datetime.now(timezone.utc)
    _seed_closed_position(session, Decimal("-60"), closed_at=now)
    session.commit()

    breaker = CircuitBreaker(threshold_usdt=Decimal("100"))
    tripped = breaker.check(session, as_of=now)
    assert tripped is False
    state = get_safety_state(session)
    assert state.breaker_tripped is False


def test_circuit_breaker_ignores_yesterday(session):
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    _seed_closed_position(session, Decimal("-500"), closed_at=yesterday)
    session.commit()

    breaker = CircuitBreaker(threshold_usdt=Decimal("100"))
    pnl = breaker.day_pnl_usdt(session, as_of=now)
    assert pnl == Decimal("0")
