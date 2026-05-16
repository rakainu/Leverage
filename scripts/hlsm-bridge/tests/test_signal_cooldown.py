"""Verify the persistent signal cooldown prevents restart-replay of recent convergence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from hlsm.convergence.events import ConvergenceEvent
from hlsm.db import Signal
from hlsm.exchange.types import OrderResult, SLTPResult, Side
from hlsm.executor import ExecutorConfig, PaperExecutor


def _ok_exchange() -> MagicMock:
    ex = MagicMock()
    ex.name = "blofin-mock"
    ex.place_order.return_value = OrderResult(
        order_id="ord-1", coin="FARTCOIN", side=Side.SHORT,
        filled_size=Decimal("2500"), avg_fill_price=Decimal("0.20"),
        notional_usdt=Decimal("500"),
    )
    ex.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="sl-1", tp_order_id="tp-1",
        sl_px=Decimal("0.205"), tp_px=Decimal("0.194"),
    )
    return ex


def _ev() -> ConvergenceEvent:
    return ConvergenceEvent(
        coin="FARTCOIN", side=Side.SHORT,
        wallet_addresses=("0xaaa", "0xbbb", "0xccc"),
        opened_at_first=datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc),
        opened_at_last=datetime(2026, 5, 15, 23, 5, tzinfo=timezone.utc),
        score_floor_used=50.0,
        window_seconds=2700,
        wallet_scores=(90.0, 80.0, 70.0),
    )


def test_cooldown_blocks_recent_signal(session):
    """A recent filled signal in cooldown window prevents a new one from firing."""
    # Seed a recent filled signal directly
    recent = Signal(
        fired_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        coin="FARTCOIN", side="short", wallet_count=3,
        wallet_addresses="0xaaa,0xbbb,0xccc",
        score_floor_used=Decimal("75"), window_seconds=2700,
        status="filled",
    )
    session.add(recent)
    session.flush()

    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(signal_cooldown_minutes=60))
    outcome = executor.execute(session, _ev())
    assert outcome.status == "skipped_already_open_on_coin"
    assert "cooldown" in (outcome.reason or "")
    # Exchange must not have been called
    ex.place_order.assert_not_called()


def test_cooldown_disabled_at_zero(session):
    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(signal_cooldown_minutes=0))
    # Pre-existing recent signal shouldn't block
    recent = Signal(
        fired_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        coin="FARTCOIN", side="short", wallet_count=3,
        wallet_addresses="0xaaa,0xbbb,0xccc",
        score_floor_used=Decimal("75"), window_seconds=2700,
        status="filled",
    )
    session.add(recent)
    session.flush()
    # Different wallet set so the one-per-coin gate also doesn't catch it (well, it does if a position is open).
    # Here we're testing cooldown=0 means cooldown gate doesn't fire — the open-on-coin gate handles the rest.
    outcome = executor.execute(session, _ev())
    # The pre-existing signal has no paper_position, so no "open on coin" — should be filled.
    assert outcome.status == "filled"


def test_cooldown_expires_after_window(session):
    """Signals older than the cooldown window should NOT block new ones."""
    old = Signal(
        fired_at=datetime.now(timezone.utc) - timedelta(minutes=90),
        coin="FARTCOIN", side="short", wallet_count=3,
        wallet_addresses="0xaaa,0xbbb,0xccc",
        score_floor_used=Decimal("75"), window_seconds=2700,
        status="filled",
    )
    session.add(old)
    session.flush()

    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(signal_cooldown_minutes=60))
    outcome = executor.execute(session, _ev())
    assert outcome.status == "filled"
