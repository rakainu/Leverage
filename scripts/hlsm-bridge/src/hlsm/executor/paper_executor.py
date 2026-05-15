"""Paper executor: turns a ConvergenceEvent into a venue order + DB rows + Telegram alert.

Owns the entry path. Exit handling lives in :mod:`hlsm.executor.exit_policy` and runs as a
periodic loop alongside this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.convergence import ConvergenceEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange import Exchange, ExchangeError, OrderRequest, Side
from hlsm.safety import gate_entry
from hlsm.safety.off_switches import EntryDecision

log = logging.getLogger(__name__)


@dataclass
class ExecutorConfig:
    per_trade_margin_usdt: Decimal = Decimal("50")
    leverage: int = 10
    hard_sl_pct: Decimal = Decimal("25")
    tp_default_pct: Decimal = Decimal("30")
    max_concurrent_positions: int = 5
    universe: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ExecutionOutcome:
    signal_id: int
    paper_position_id: int | None
    status: str  # filled | skipped_* | error
    reason: str
    venue_order_id: str | None = None


class PaperExecutor:
    """Turns ConvergenceEvent into venue order + DB rows. Alerts callback is optional."""

    def __init__(self, *, exchange: Exchange, config: ExecutorConfig,
                 on_signal: Callable[[Signal, PaperPosition | None, ExecutionOutcome], None] | None = None) -> None:
        self.exchange = exchange
        self.config = config
        self.on_signal = on_signal

    def _count_open_positions(self, session: Session) -> int:
        return session.execute(
            select(PaperPosition.id).where(PaperPosition.status == "open")
        ).scalars().all().__len__()

    def execute(self, session: Session, ev: ConvergenceEvent) -> ExecutionOutcome:
        """Process a single convergence event. Always persists a Signal row, even if skipped."""
        # Always-create signal so the dashboard sees every detected convergence.
        signal = Signal(
            fired_at=datetime.now(timezone.utc),
            coin=ev.coin,
            side=ev.side.value,
            wallet_count=ev.wallet_count,
            wallet_addresses=",".join(ev.wallet_addresses),
            score_floor_used=ev.score_floor_used,
            window_seconds=ev.window_seconds,
            status="pending",
        )
        session.add(signal)
        session.flush()  # get signal.id

        # Gate entry
        open_count = self._count_open_positions(session)
        gate = gate_entry(
            session=session,
            coin=ev.coin,
            open_paper_count=open_count,
            max_concurrent=self.config.max_concurrent_positions,
            universe=self.config.universe or None,
        )
        if not gate.allowed:
            signal.status = gate.decision.value
            signal.reason = gate.reason
            outcome = ExecutionOutcome(signal.id, None, gate.decision.value, gate.reason)
            if self.on_signal is not None:
                try:
                    self.on_signal(signal, None, outcome)
                except Exception:  # noqa: BLE001
                    log.exception("on_signal callback failed (skip path)")
            return outcome

        # Place order
        try:
            order = self.exchange.place_order(OrderRequest(
                coin=ev.coin,
                side=ev.side,
                margin_usdt=self.config.per_trade_margin_usdt,
                leverage=self.config.leverage,
                client_order_id=f"hlsm-{signal.id}",
            ))
        except ExchangeError as e:
            signal.status = "error"
            signal.reason = f"place_order: {e}"[:128]
            outcome = ExecutionOutcome(signal.id, None, "error", str(e))
            if self.on_signal is not None:
                try:
                    self.on_signal(signal, None, outcome)
                except Exception:  # noqa: BLE001
                    log.exception("on_signal callback failed (error path)")
            return outcome

        # Attach SL + TP
        try:
            sltp = self.exchange.attach_sl_tp(
                coin=ev.coin,
                side=ev.side,
                entry_px=order.avg_fill_price,
                sl_pct=self.config.hard_sl_pct,
                tp_pct=self.config.tp_default_pct,
                size=order.filled_size,
            )
        except ExchangeError as e:
            # Position is open but unprotected — close immediately for safety
            log.error("attach_sl_tp failed; closing newly-opened position to avoid unprotected exposure: %s", e)
            try:
                self.exchange.close_position(coin=ev.coin, reason="error")
            except Exception:  # noqa: BLE001
                log.exception("emergency close also failed")
            signal.status = "error"
            signal.reason = f"attach_sl_tp: {e}"[:128]
            outcome = ExecutionOutcome(signal.id, None, "error", str(e))
            if self.on_signal is not None:
                try:
                    self.on_signal(signal, None, outcome)
                except Exception:  # noqa: BLE001
                    log.exception("on_signal callback failed (unprotected close path)")
            return outcome

        # Persist paper position
        pp = PaperPosition(
            signal_id=signal.id,
            venue=self.exchange.name,
            venue_order_id=order.order_id,
            venue_sl_order_id=sltp.sl_order_id,
            venue_tp_order_id=sltp.tp_order_id,
            coin=ev.coin,
            side=ev.side.value,
            margin_usdt=self.config.per_trade_margin_usdt,
            leverage=self.config.leverage,
            notional_usdt=order.notional_usdt,
            entry_px=order.avg_fill_price,
            sl_px=sltp.sl_px,
            tp_px=sltp.tp_px,
            opened_at=datetime.now(timezone.utc),
            status="open",
        )
        session.add(pp)
        signal.status = "filled"
        signal.reason = None
        session.flush()

        outcome = ExecutionOutcome(signal.id, pp.id, "filled", "ok", venue_order_id=order.order_id)
        if self.on_signal is not None:
            try:
                self.on_signal(signal, pp, outcome)
            except Exception:  # noqa: BLE001
                log.exception("on_signal callback failed (fill path)")
        return outcome
