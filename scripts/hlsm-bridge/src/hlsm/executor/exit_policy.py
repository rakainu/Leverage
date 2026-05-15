"""Exit policy: median wallet-exit rule + hard SL/TP observation + drain handling.

Hard SL and TP are placed on the venue at entry (algo orders). The policy here observes
position state and treats venue closure as authoritative. The MEDIAN rule is the extra
exit trigger we own: when >=2 of 3 originally-converged wallets have exited their HL position,
we proactively close on the venue (cancel SL/TP first).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.convergence.events import WalletCloseEvent
from hlsm.db import PaperPosition, Signal
from hlsm.exchange import Exchange, Side
from hlsm.safety.state import get_safety_state

log = logging.getLogger(__name__)


class ExitDecision(str, Enum):
    HOLD = "hold"
    CLOSE_MEDIAN = "wallet_exit"
    CLOSE_SL = "sl"
    CLOSE_TP = "tp"
    CLOSE_DRAIN = "drain"


@dataclass
class ExitPolicyConfig:
    exit_rule: str = "median"   # any | median | all  (1/N, 2/N rounding up, N/N)
    hard_sl_pct: Decimal = Decimal("25")
    tp_default_pct: Decimal = Decimal("30")


def _threshold(rule: str, n: int) -> int:
    if rule == "any":
        return 1
    if rule == "all":
        return n
    # median = strict majority (more than half)
    return n // 2 + 1


class ExitPolicy:
    """Evaluates each open paper position against exit rules; closes via the exchange when triggered."""

    def __init__(self, *, exchange: Exchange, config: ExitPolicyConfig,
                 on_close: Callable[[PaperPosition, ExitDecision, Decimal], None] | None = None) -> None:
        self.exchange = exchange
        self.config = config
        self.on_close = on_close

    def _wallets_exited_count(self, session: Session, signal: Signal,
                              originals: list[str]) -> int:
        """Count how many of the originals have closed their position since signal fired."""
        from hlsm.db import Event
        # Look for close/flip events on the same coin from any of the originals since signal.fired_at
        rows = session.execute(
            select(Event.wallet_address).where(
                Event.wallet_address.in_(originals),
                Event.coin == signal.coin,
                Event.kind.in_(["close", "flip"]),
                Event.ts >= signal.fired_at,
            )
        ).all()
        return len({r[0] for r in rows})

    def decide(self, session: Session, pp: PaperPosition) -> tuple[ExitDecision, Decimal | None]:
        """Return the exit decision for one position + the close-price hint (if available)."""
        state = get_safety_state(session)
        if state.drain_mode:
            return ExitDecision.CLOSE_DRAIN, None

        # Check venue-side state. If venue says position is flat, SL or TP fired.
        try:
            venue_pos = self.exchange.get_position(pp.coin)
        except Exception:  # noqa: BLE001
            venue_pos = None

        if venue_pos is None:
            # Position closed on venue. Determine reason by which side of entry we'd be at.
            # If we don't have a live price, default to SL — safer to count it as a loss.
            return ExitDecision.CLOSE_SL, None

        # Mark-based SL / TP check (defensive; venue should have done this).
        mark = venue_pos.mark_px
        if mark is not None and mark > 0:
            if pp.side == Side.LONG.value:
                if mark <= Decimal(pp.sl_px):
                    return ExitDecision.CLOSE_SL, mark
                if mark >= Decimal(pp.tp_px):
                    return ExitDecision.CLOSE_TP, mark
            else:
                if mark >= Decimal(pp.sl_px):
                    return ExitDecision.CLOSE_SL, mark
                if mark <= Decimal(pp.tp_px):
                    return ExitDecision.CLOSE_TP, mark

        # Median wallet-exit rule
        signal = session.get(Signal, pp.signal_id)
        if signal is not None:
            originals = signal.wallet_addresses.split(",")
            n = len(originals)
            threshold = _threshold(self.config.exit_rule, n)
            exited = self._wallets_exited_count(session, signal, originals)
            if exited >= threshold:
                return ExitDecision.CLOSE_MEDIAN, mark

        return ExitDecision.HOLD, mark

    def maybe_close(self, session: Session, pp: PaperPosition) -> ExitDecision:
        """Run :meth:`decide`; if exit triggered, close on the venue and update DB."""
        decision, mark_hint = self.decide(session, pp)
        if decision == ExitDecision.HOLD:
            return decision

        # Close on venue (no-op if already flat).
        try:
            self.exchange.cancel_protective_orders(coin=pp.coin)
        except Exception:  # noqa: BLE001
            log.warning("cancel_protective_orders failed", exc_info=True)
        try:
            order = self.exchange.close_position(coin=pp.coin, reason=decision.value)
        except Exception:  # noqa: BLE001
            log.exception("close_position failed for %s; marking position as error-closed", pp.coin)
            order = None

        exit_px = Decimal(order.avg_fill_price) if order is not None else (mark_hint or Decimal(pp.entry_px))
        entry = Decimal(pp.entry_px)
        # Signed PnL: long profits when exit > entry; short profits when exit < entry.
        if pp.side == Side.LONG.value:
            move_pct = ((exit_px - entry) / entry) * Decimal(100)
        else:
            move_pct = ((entry - exit_px) / entry) * Decimal(100)
        pnl_pct = (move_pct * Decimal(pp.leverage)).quantize(Decimal("0.0001"))
        pnl_usdt = (Decimal(pp.margin_usdt) * pnl_pct / Decimal(100)).quantize(Decimal("0.00000001"))

        pp.status = "closed"
        pp.close_reason = decision.value
        pp.closed_at = datetime.now(timezone.utc)
        pp.exit_px = exit_px
        pp.realized_pnl_usdt = pnl_usdt
        pp.realized_pnl_pct = pnl_pct
        session.flush()

        if self.on_close is not None:
            try:
                self.on_close(pp, decision, pnl_usdt)
            except Exception:  # noqa: BLE001
                log.exception("on_close callback failed")

        return decision

    def sweep_open(self, session: Session) -> dict[str, int]:
        """One pass over all open paper_positions. Returns counts per decision."""
        opens = session.execute(select(PaperPosition).where(PaperPosition.status == "open")).scalars().all()
        counts: dict[str, int] = {}
        for pp in opens:
            decision = self.maybe_close(session, pp)
            counts[decision.value] = counts.get(decision.value, 0) + 1
        return counts
