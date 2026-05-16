"""Paper executor: turns a ConvergenceEvent into a venue order + DB rows + Telegram alert.

Owns the entry path. Exit handling lives in :mod:`hlsm.executor.exit_policy` and runs as a
periodic loop alongside this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    # Persistent cooldown: skip a (coin, side) convergence if we already fired one or
    # closed a paper_position on the same (coin, side) within this many minutes.
    # Survives container restarts (queried from DB), unlike the in-memory dedup map
    # in ConvergenceDetector.
    signal_cooldown_minutes: int = 60


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

    def _already_open_on_coin(self, session: Session, coin: str) -> bool:
        existing = session.execute(
            select(PaperPosition.id).where(
                PaperPosition.coin == coin.upper(),
                PaperPosition.status == "open",
            )
        ).first()
        return existing is not None

    def _within_signal_cooldown(self, session: Session, coin: str, side: str) -> Signal | None:
        """Return the most recent signal on (coin, side) within the cooldown window, if any.

        Persistent across container restarts (queries the signals table). Combined with the
        in-memory ConvergenceDetector cooldown, this prevents the system from re-issuing a
        trade on the same (coin, side) just because the in-memory dedup map got wiped.
        """
        if self.config.signal_cooldown_minutes <= 0:
            return None
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.config.signal_cooldown_minutes)
        recent = session.execute(
            select(Signal).where(
                Signal.coin == coin.upper(),
                Signal.side == side,
                Signal.fired_at >= cutoff,
                Signal.status.in_(["filled", "pending"]),
            ).order_by(Signal.fired_at.desc()).limit(1)
        ).scalar_one_or_none()
        return recent

    def execute(self, session: Session, ev: ConvergenceEvent) -> ExecutionOutcome:
        """Process a single convergence event. Always persists a Signal row, even if skipped."""
        # Persistent cooldown check FIRST, before adding our own signal row to avoid
        # finding ourselves. Survives container restarts (queried from DB).
        recent = self._within_signal_cooldown(session, ev.coin, ev.side.value)

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

        if recent is not None:
            signal.status = "skipped_already_open_on_coin"
            signal.reason = (
                f"cooldown: previous signal #{recent.id} at {recent.fired_at.isoformat()} "
                f"within {self.config.signal_cooldown_minutes}min window"
            )[:128]
            outcome = ExecutionOutcome(signal.id, None, signal.status, signal.reason)
            if self.on_signal is not None:
                try:
                    self.on_signal(signal, None, outcome)
                except Exception:  # noqa: BLE001
                    log.exception("on_signal callback failed (cooldown path)")
            return outcome

        # Gate entry
        open_count = self._count_open_positions(session)
        already_open = self._already_open_on_coin(session, ev.coin)
        gate = gate_entry(
            session=session,
            coin=ev.coin,
            open_paper_count=open_count,
            max_concurrent=self.config.max_concurrent_positions,
            universe=self.config.universe or None,
            already_open_on_coin=already_open,
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
                client_order_id=f"hlsm{signal.id}",
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

        # Cancel any orphan/stale protective orders for this coin BEFORE attaching new ones,
        # so we never end up with stacked SL/TP orders on the same position.
        try:
            stale = self.exchange.cancel_protective_orders(coin=ev.coin)
            if stale:
                log.info("cancelled %d stale protective orders on %s before attach", stale, ev.coin)
        except Exception:  # noqa: BLE001
            log.warning("cancel_protective_orders pre-attach raised", exc_info=True)

        # Attach SL + TP
        try:
            sltp = self.exchange.attach_sl_tp(
                coin=ev.coin,
                side=ev.side,
                entry_px=order.avg_fill_price,
                sl_pct=self.config.hard_sl_pct,
                tp_pct=self.config.tp_default_pct,
                size=order.filled_size,
                leverage=self.config.leverage,
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
