"""Verify the one-position-per-coin guard prevents stacking on the same coin."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from hlsm.convergence.events import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange.types import OrderResult, SLTPResult, Side
from hlsm.executor import ExecutorConfig, PaperExecutor


def _ok_exchange() -> MagicMock:
    ex = MagicMock()
    ex.name = "blofin-mock"
    ex.place_order.return_value = OrderResult(
        order_id="ord-2", coin="FARTCOIN", side=Side.SHORT,
        filled_size=Decimal("2500"), avg_fill_price=Decimal("0.20"),
        notional_usdt=Decimal("500"),
    )
    ex.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="sl-2", tp_order_id="tp-2",
        sl_px=Decimal("0.205"), tp_px=Decimal("0.194"),
    )
    return ex


def _ev(coin: str = "FARTCOIN", side: Side = Side.SHORT) -> ConvergenceEvent:
    return ConvergenceEvent(
        coin=coin, side=side,
        wallet_addresses=("0xaaa", "0xbbb", "0xccc"),
        opened_at_first=datetime(2026, 5, 15, 23, 0, tzinfo=timezone.utc),
        opened_at_last=datetime(2026, 5, 15, 23, 5, tzinfo=timezone.utc),
        score_floor_used=50.0,
        window_seconds=2700,
        wallet_scores=(90.0, 80.0, 70.0),
    )


def test_second_convergence_same_coin_is_skipped(session):
    ex = _ok_exchange()
    # Disable persistent cooldown so we isolate the open-on-coin gate
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(signal_cooldown_minutes=0))

    first = executor.execute(session, _ev())
    assert first.status == "filled"
    session.commit()

    # New convergence on same coin (e.g., after restart wiped the cooldown map)
    second = executor.execute(session, _ev())
    assert second.status == "skipped_already_open_on_coin"
    assert second.paper_position_id is None
    sig = session.get(Signal, second.signal_id)
    assert sig.status == "skipped_already_open_on_coin"
    assert "already have an open" in (sig.reason or "")
    # Exchange should not have been called twice
    assert ex.place_order.call_count == 1


def test_convergence_on_different_coin_allowed_when_first_still_open(session):
    ex = _ok_exchange()
    # Disable persistent cooldown so we isolate the open-on-coin gate
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(signal_cooldown_minutes=0))

    first = executor.execute(session, _ev(coin="FARTCOIN"))
    assert first.status == "filled"
    session.commit()

    ex.place_order.return_value = OrderResult(
        order_id="ord-3", coin="PEPE", side=Side.LONG,
        filled_size=Decimal("1000000"), avg_fill_price=Decimal("0.00001"),
        notional_usdt=Decimal("10"),
    )
    ex.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="sl-3", tp_order_id="tp-3",
        sl_px=Decimal("0.0000075"), tp_px=Decimal("0.00001300"),
    )
    second = executor.execute(session, _ev(coin="PEPE", side=Side.LONG))
    assert second.status == "filled"
    assert ex.place_order.call_count == 2
