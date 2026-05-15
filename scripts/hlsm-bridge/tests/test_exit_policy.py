"""Exit policy unit tests. Mocks exchange + DB so we test logic only."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from hlsm.db import Event, PaperPosition, Signal, Wallet
from hlsm.executor.exit_policy import ExitDecision, ExitPolicy, ExitPolicyConfig, _threshold
from hlsm.exchange.types import PositionInfo, Side
from hlsm.safety.off_switches import apply_drain


def test_threshold_rules():
    assert _threshold("any", 3) == 1
    assert _threshold("median", 3) == 2
    assert _threshold("all", 3) == 3
    assert _threshold("median", 4) == 3
    assert _threshold("median", 5) == 3


def _seed_signal_and_position(session, *, coin="PEPE", side="long",
                              entry_px=Decimal("0.00001"), sl_px=Decimal("0.0000075"),
                              tp_px=Decimal("0.000013"), wallets=("0xaaa", "0xbbb", "0xccc")):
    for addr in wallets:
        session.add(Wallet(address=addr, current_score=Decimal("85"), trade_count=200))
    signal = Signal(
        fired_at=datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc),
        coin=coin, side=side,
        wallet_count=len(wallets),
        wallet_addresses=",".join(wallets),
        score_floor_used=Decimal("75"),
        window_seconds=2700,
        status="filled",
    )
    session.add(signal)
    session.flush()
    pp = PaperPosition(
        signal_id=signal.id, venue="blofin",
        coin=coin, side=side,
        margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
        entry_px=entry_px, sl_px=sl_px, tp_px=tp_px,
        status="open",
    )
    session.add(pp)
    session.flush()
    return signal, pp


def _fake_exchange(*, position: PositionInfo | None) -> MagicMock:
    ex = MagicMock()
    ex.name = "blofin-mock"
    ex.get_position.return_value = position
    ex.cancel_protective_orders.return_value = 0
    return ex


def test_hold_when_position_open_no_triggers(session):
    signal, pp = _seed_signal_and_position(session)
    # mark is between SL and TP (above entry but below TP)
    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.000011"), unrealized_pnl_usdt=Decimal("1"),
                      leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.HOLD


def test_sl_trigger_on_long(session):
    signal, pp = _seed_signal_and_position(session)
    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000070"),  # below SL 0.0000075
                      unrealized_pnl_usdt=Decimal("-12"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.CLOSE_SL


def test_tp_trigger_on_long(session):
    signal, pp = _seed_signal_and_position(session)
    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.000014"),  # above TP 0.000013
                      unrealized_pnl_usdt=Decimal("30"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.CLOSE_TP


def test_sl_trigger_on_short(session):
    signal, pp = _seed_signal_and_position(
        session, side="short",
        entry_px=Decimal("0.00001"),
        sl_px=Decimal("0.0000125"),  # short SL above entry
        tp_px=Decimal("0.0000070"),  # short TP below entry
    )
    pos = PositionInfo(coin="PEPE", side=Side.SHORT,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000130"),  # above SL — short loss
                      unrealized_pnl_usdt=Decimal("-15"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.CLOSE_SL


def test_median_wallet_exit_rule_fires(session):
    signal, pp = _seed_signal_and_position(session)
    # 2 of 3 originally-converged wallets have closed their HL position
    later = signal.fired_at.replace(hour=14)
    session.add(Event(wallet_address="0xaaa", ts=later, coin="PEPE", side="long",
                      kind="close", sz_after=Decimal("0")))
    session.add(Event(wallet_address="0xbbb", ts=later, coin="PEPE", side="long",
                      kind="close", sz_after=Decimal("0")))
    session.flush()

    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000105"),  # not at SL or TP
                      unrealized_pnl_usdt=Decimal("5"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.CLOSE_MEDIAN


def test_median_does_not_fire_when_only_one_exited(session):
    signal, pp = _seed_signal_and_position(session)
    later = signal.fired_at.replace(hour=14)
    session.add(Event(wallet_address="0xaaa", ts=later, coin="PEPE", side="long",
                      kind="close", sz_after=Decimal("0")))
    session.flush()

    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000105"),
                      unrealized_pnl_usdt=Decimal("5"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.HOLD


def test_drain_mode_closes_everything(session):
    signal, pp = _seed_signal_and_position(session)
    apply_drain(session)
    session.commit()

    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000105"),
                      unrealized_pnl_usdt=Decimal("5"), leverage=10)
    ex = _fake_exchange(position=pos)
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision, _ = pol.decide(session, pp)
    assert decision == ExitDecision.CLOSE_DRAIN


def test_close_writes_realized_pnl_for_long_loss(session):
    signal, pp = _seed_signal_and_position(session)
    pos = PositionInfo(coin="PEPE", side=Side.LONG,
                      size=Decimal("1000"), entry_px=Decimal("0.00001"),
                      mark_px=Decimal("0.0000070"),
                      unrealized_pnl_usdt=Decimal("-12"), leverage=10)
    ex = _fake_exchange(position=pos)
    # Mock close_position to return a sane fill at SL price
    from hlsm.exchange.types import OrderResult
    ex.close_position.return_value = OrderResult(
        order_id="close-1", coin="PEPE", side=Side.LONG,
        filled_size=Decimal("1000"), avg_fill_price=Decimal("0.0000075"),
        notional_usdt=Decimal("7.5"),
    )
    pol = ExitPolicy(exchange=ex, config=ExitPolicyConfig())
    decision = pol.maybe_close(session, pp)
    assert decision == ExitDecision.CLOSE_SL
    assert pp.status == "closed"
    assert pp.close_reason == "sl"
    assert pp.realized_pnl_pct is not None
    # -25% move * 10x leverage = -250% on margin
    assert Decimal(pp.realized_pnl_pct).quantize(Decimal("1")) == Decimal("-250")
    # -250% of $50 margin = -$125 PnL
    assert Decimal(pp.realized_pnl_usdt).quantize(Decimal("0.01")) == Decimal("-125.00")
