"""Daily-loss circuit breaker.

Computes cumulative day PnL from paper_positions closed today (UTC). When the threshold
is crossed downward, calls trip_breaker which mutates the safety state to paused.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from hlsm.db import PaperPosition
from hlsm.safety.off_switches import trip_breaker
from hlsm.safety.state import get_safety_state


class CircuitBreaker:
    """Stateless checker. Call :meth:`check` periodically (e.g. each tick of the exit loop)."""

    def __init__(self, *, threshold_usdt: Decimal,
                 on_trip: Callable[[Decimal], None] | None = None) -> None:
        self.threshold = abs(threshold_usdt)  # always positive; PnL compared to -threshold
        self.on_trip = on_trip

    def day_pnl_usdt(self, session: Session, *, as_of: datetime | None = None) -> Decimal:
        """Return cumulative realized PnL in USDT for positions closed today (UTC)."""
        now = as_of or datetime.now(timezone.utc)
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        rows = session.execute(
            select(PaperPosition.realized_pnl_usdt).where(
                PaperPosition.status == "closed",
                PaperPosition.closed_at >= day_start,
                PaperPosition.closed_at < day_end,
            )
        ).all()
        total = Decimal("0")
        for (val,) in rows:
            if val is not None:
                total += Decimal(val)
        return total

    def check(self, session: Session, *, as_of: datetime | None = None) -> bool:
        """Return True if the breaker is now (or already) tripped. False otherwise."""
        state = get_safety_state(session)
        if state.breaker_tripped:
            return True
        pnl = self.day_pnl_usdt(session, as_of=as_of)
        if pnl <= -self.threshold:
            trip_breaker(session)
            if self.on_trip is not None:
                self.on_trip(pnl)
            return True
        return False
