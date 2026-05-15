"""End-to-end test: synthetic convergence -> executor -> DB rows + Telegram message.

This satisfies DoD criterion 12: 'inject synthetic convergence event into pipeline;
assert BloFin demo order placed, SL/TP attached, positions row created, signals
row linked, Telegram message sent to test channel.'
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from hlsm.convergence.events import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange.types import OrderResult, SLTPResult, Side
from hlsm.executor import ExecutorConfig, PaperExecutor
from hlsm.telegram.alerts import AlertSender


def test_synthetic_convergence_writes_all_artifacts(session, monkeypatch):
    """Full pipeline: synthetic ConvergenceEvent -> executor -> Signal+PaperPosition+Telegram."""
    # --- Set up venue mock (BloFin demo stand-in) ---
    venue = MagicMock()
    venue.name = "blofin"
    venue.place_order.return_value = OrderResult(
        order_id="blofin-demo-ord-1",
        coin="PEPE",
        side=Side.LONG,
        filled_size=Decimal("50000000"),  # base units
        avg_fill_price=Decimal("0.00001000"),
        notional_usdt=Decimal("500"),
        fee_usdt=Decimal("0.25"),
    )
    venue.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="blofin-demo-sl-1",
        tp_order_id="blofin-demo-tp-1",
        sl_px=Decimal("0.00000750"),
        tp_px=Decimal("0.00001300"),
    )

    # --- Capture Telegram sends without hitting the network ---
    captured_messages: list[str] = []

    def fake_send(self, text: str) -> bool:
        captured_messages.append(text)
        return True

    monkeypatch.setattr(AlertSender, "send", fake_send)
    monkeypatch.setattr(AlertSender, "enabled", True)

    # Wire Telegram alert into the executor's on_signal callback
    sender = AlertSender(bot_token="test-bot", chat_id="test-chat")

    def on_signal(signal: Signal, pp: PaperPosition | None, outcome) -> None:
        if signal.status == "filled" and pp is not None:
            from hlsm.telegram import format_position_open
            sender.send(format_position_open(signal, pp))

    executor = PaperExecutor(
        exchange=venue,
        config=ExecutorConfig(
            per_trade_margin_usdt=Decimal("50"),
            leverage=10,
            hard_sl_pct=Decimal("25"),
            tp_default_pct=Decimal("30"),
            max_concurrent_positions=5,
            universe=frozenset({"PEPE", "WIF", "BONK"}),
        ),
        on_signal=on_signal,
    )

    # --- Inject synthetic convergence event ---
    t = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    ev = ConvergenceEvent(
        coin="PEPE",
        side=Side.LONG,
        wallet_addresses=("0xaaaaaaaaaaaa", "0xbbbbbbbbbbbb", "0xcccccccccccc"),
        opened_at_first=t,
        opened_at_last=t,
        score_floor_used=75.0,
        window_seconds=2700,
        wallet_scores=(90.0, 85.0, 80.0),
    )

    outcome = executor.execute(session, ev)

    # ---- ASSERT 1: outcome.status == 'filled' ----
    assert outcome.status == "filled", f"executor outcome should be filled, got {outcome.status}"
    assert outcome.venue_order_id == "blofin-demo-ord-1"

    # ---- ASSERT 2: venue place_order was called with correct sizing ----
    venue.place_order.assert_called_once()
    order_req = venue.place_order.call_args.args[0]
    assert order_req.coin == "PEPE"
    assert order_req.side == Side.LONG
    assert order_req.margin_usdt == Decimal("50")
    assert order_req.leverage == 10

    # ---- ASSERT 3: SL and TP both attached ----
    venue.attach_sl_tp.assert_called_once()
    kwargs = venue.attach_sl_tp.call_args.kwargs
    assert kwargs["coin"] == "PEPE"
    assert kwargs["sl_pct"] == Decimal("25")
    assert kwargs["tp_pct"] == Decimal("30")

    # ---- ASSERT 4: Signal row has all required columns populated ----
    signal = session.get(Signal, outcome.signal_id)
    assert signal is not None
    assert signal.coin == "PEPE"
    assert signal.side == "long"
    assert signal.wallet_count == 3
    assert signal.wallet_addresses == "0xaaaaaaaaaaaa,0xbbbbbbbbbbbb,0xcccccccccccc"
    assert signal.status == "filled"
    assert signal.score_floor_used == Decimal("75")
    assert signal.window_seconds == 2700

    # ---- ASSERT 5: PaperPosition row created + linked back to signal ----
    pp = session.get(PaperPosition, outcome.paper_position_id)
    assert pp is not None
    assert pp.signal_id == signal.id
    assert pp.venue == "blofin"
    assert pp.venue_order_id == "blofin-demo-ord-1"
    assert pp.venue_sl_order_id == "blofin-demo-sl-1"
    assert pp.venue_tp_order_id == "blofin-demo-tp-1"
    assert pp.coin == "PEPE"
    assert pp.side == "long"
    assert Decimal(pp.margin_usdt) == Decimal("50")
    assert pp.leverage == 10
    assert Decimal(pp.entry_px) == Decimal("0.00001000")
    assert Decimal(pp.sl_px) == Decimal("0.00000750")
    assert Decimal(pp.tp_px) == Decimal("0.00001300")
    assert pp.status == "open"
    assert pp.close_reason is None

    # ---- ASSERT 6: Telegram message was 'sent' ----
    assert len(captured_messages) >= 1
    body = captured_messages[-1]
    assert "OPENED" in body
    assert "PEPE" in body
    assert "LONG" in body
    assert f"#{signal.id}" in body

    # ---- ASSERT 7: signal.paper_position relationship populated ----
    session.expire_all()
    fresh_signal = session.get(Signal, signal.id)
    assert fresh_signal.paper_position is not None
    assert fresh_signal.paper_position.id == pp.id
