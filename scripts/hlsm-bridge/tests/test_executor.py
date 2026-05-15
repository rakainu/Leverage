"""Paper executor unit tests. Exchange is mocked; DB is real (in-memory)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from hlsm.convergence.events import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange.types import OrderResult, SLTPResult, Side
from hlsm.executor import ExecutorConfig, PaperExecutor
from hlsm.safety.off_switches import apply_pause, apply_pause_coin


def _convergence_event(coin: str = "PEPE", side: Side = Side.LONG) -> ConvergenceEvent:
    t = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
    return ConvergenceEvent(
        coin=coin, side=side,
        wallet_addresses=("0xaaa", "0xbbb", "0xccc"),
        opened_at_first=t,
        opened_at_last=t,
        score_floor_used=75.0,
        window_seconds=2700,
        wallet_scores=(90.0, 85.0, 80.0),
    )


def _ok_exchange() -> MagicMock:
    ex = MagicMock()
    ex.name = "blofin-mock"
    ex.place_order.return_value = OrderResult(
        order_id="ord-1", coin="PEPE", side=Side.LONG,
        filled_size=Decimal("1000000"),
        avg_fill_price=Decimal("0.00001"),
        notional_usdt=Decimal("10"),
    )
    ex.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="sl-1", tp_order_id="tp-1",
        sl_px=Decimal("0.0000075"), tp_px=Decimal("0.000013"),
    )
    return ex


def test_filled_path_writes_signal_and_paper_position(session):
    ex = _ok_exchange()
    callbacks: list[tuple[Signal, PaperPosition | None, object]] = []
    executor = PaperExecutor(
        exchange=ex,
        config=ExecutorConfig(),
        on_signal=lambda s, p, o: callbacks.append((s, p, o)),
    )

    outcome = executor.execute(session, _convergence_event())
    assert outcome.status == "filled"
    assert outcome.paper_position_id is not None

    # DB rows
    signal = session.get(Signal, outcome.signal_id)
    assert signal is not None
    assert signal.status == "filled"
    assert signal.coin == "PEPE"
    assert signal.wallet_count == 3

    pp = session.get(PaperPosition, outcome.paper_position_id)
    assert pp is not None
    assert pp.signal_id == signal.id
    assert pp.coin == "PEPE"
    assert pp.side == "long"
    assert pp.venue == "blofin-mock"
    assert pp.venue_order_id == "ord-1"
    assert pp.venue_sl_order_id == "sl-1"
    assert pp.venue_tp_order_id == "tp-1"
    assert Decimal(pp.margin_usdt) == Decimal("50")
    assert pp.leverage == 10
    assert Decimal(pp.entry_px) == Decimal("0.00001")

    # Exchange calls
    ex.place_order.assert_called_once()
    ex.attach_sl_tp.assert_called_once()

    # Callback received the row
    assert len(callbacks) == 1
    s, p, o = callbacks[0]
    assert s.id == signal.id
    assert p is not None
    assert o.status == "filled"


def test_global_pause_skips_entry_but_still_records_signal(session):
    apply_pause(session)
    session.commit()
    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig())

    outcome = executor.execute(session, _convergence_event())
    assert outcome.status == "skipped_paused"
    assert outcome.paper_position_id is None
    signal = session.get(Signal, outcome.signal_id)
    assert signal.status == "skipped_paused"
    ex.place_order.assert_not_called()


def test_per_coin_pause_skips_only_that_coin(session):
    apply_pause_coin(session, "PEPE")
    session.commit()
    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig())

    blocked = executor.execute(session, _convergence_event(coin="PEPE"))
    # WIF should pass through (still places order, mock returns ord-1 either way)
    ex.place_order.return_value = OrderResult(
        order_id="ord-2", coin="WIF", side=Side.LONG,
        filled_size=Decimal("1000"), avg_fill_price=Decimal("3.0"),
        notional_usdt=Decimal("3000"),
    )
    ex.attach_sl_tp.return_value = SLTPResult(
        sl_order_id="sl-2", tp_order_id="tp-2",
        sl_px=Decimal("2.25"), tp_px=Decimal("3.9"),
    )
    allowed = executor.execute(session, _convergence_event(coin="WIF"))
    assert blocked.status == "skipped_coin_paused"
    assert allowed.status == "filled"


def test_max_concurrent_blocks_when_limit_reached(session):
    # Seed 5 open paper_positions
    for i in range(5):
        signal = Signal(
            coin=f"COIN{i}", side="long", wallet_count=3,
            wallet_addresses="a,b,c", score_floor_used=Decimal("75"),
            window_seconds=2700, status="filled",
        )
        session.add(signal)
        session.flush()
        session.add(PaperPosition(
            signal_id=signal.id, venue="blofin-mock",
            coin=f"COIN{i}", side="long",
            margin_usdt=Decimal("50"), leverage=10, notional_usdt=Decimal("500"),
            entry_px=Decimal("1"), sl_px=Decimal("0.75"), tp_px=Decimal("1.3"),
            status="open",
        ))
    session.commit()

    ex = _ok_exchange()
    executor = PaperExecutor(exchange=ex, config=ExecutorConfig(max_concurrent_positions=5))
    outcome = executor.execute(session, _convergence_event())
    assert outcome.status == "skipped_max_concurrent"
    ex.place_order.assert_not_called()


def test_universe_blocks_off_list_coin(session):
    ex = _ok_exchange()
    executor = PaperExecutor(
        exchange=ex,
        config=ExecutorConfig(universe=frozenset({"PEPE", "WIF"})),
    )
    outcome = executor.execute(session, _convergence_event(coin="BTC"))
    assert outcome.status == "skipped_universe"
    ex.place_order.assert_not_called()
