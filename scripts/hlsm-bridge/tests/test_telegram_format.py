"""Telegram message formatting tests."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from hlsm.convergence import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange.types import Side
from hlsm.executor.exit_policy import ExitDecision
from hlsm.telegram import (
    format_convergence,
    format_position_close,
    format_position_open,
)


def test_format_convergence_includes_key_facts():
    ev = ConvergenceEvent(
        coin="PEPE", side=Side.LONG,
        wallet_addresses=("0xaaaaaa1111", "0xbbbbbb2222", "0xcccccc3333"),
        opened_at_first=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        opened_at_last=datetime(2026, 5, 15, 12, 20, tzinfo=timezone.utc),
        score_floor_used=75.0,
        window_seconds=2700,
        wallet_scores=(90.0, 85.0, 80.0),
    )
    msg = format_convergence(ev)
    assert "CONVERGENCE" in msg
    assert "PEPE" in msg
    assert "LONG" in msg
    assert "45min" in msg or "45 min" in msg


def test_format_position_open():
    signal = Signal(
        id=42, coin="WIF", side="long", wallet_count=3,
        wallet_addresses="a,b,c", score_floor_used=Decimal("75"),
        window_seconds=2700, status="filled",
    )
    pp = PaperPosition(
        id=1, signal_id=42, venue="blofin",
        coin="WIF", side="long",
        margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
        entry_px=Decimal("3.00"), sl_px=Decimal("2.25"), tp_px=Decimal("3.9"),
        status="open",
    )
    msg = format_position_open(signal, pp)
    assert "OPENED" in msg
    assert "WIF" in msg
    assert "$50" in msg and "10x" in msg
    assert "#42" in msg


def test_format_position_close_with_loss():
    pp = PaperPosition(
        id=1, signal_id=42, venue="blofin",
        coin="PEPE", side="long",
        margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
        entry_px=Decimal("0.00001"), sl_px=Decimal("0.0000075"), tp_px=Decimal("0.000013"),
        exit_px=Decimal("0.0000075"),
        realized_pnl_usdt=Decimal("-12.5"),
        realized_pnl_pct=Decimal("-25"),
        status="closed", close_reason="sl",
    )
    msg = format_position_close(pp, ExitDecision.CLOSE_SL, Decimal("-12.5"))
    assert "CLOSED" in msg
    assert "PEPE" in msg
    assert "$-12.50" in msg or "-$12.50" in msg
    assert "sl" in msg.lower()
