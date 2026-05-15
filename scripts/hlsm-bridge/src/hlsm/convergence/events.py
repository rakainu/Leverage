"""Convergence event types. Frozen for safety; carry enough to reproduce the trigger."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from hlsm.exchange.types import Side


@dataclass(frozen=True)
class WalletOpenEvent:
    """A ranked wallet just opened a position. Feeds the convergence detector."""

    wallet_address: str
    score: float
    coin: str
    side: Side
    ts: datetime
    px: Decimal | None = None


@dataclass(frozen=True)
class WalletCloseEvent:
    """A ranked wallet just closed (or flipped out of) a position. Drives the median-exit rule."""

    wallet_address: str
    coin: str
    side: Side  # the side they had been in (now flat or flipped)
    ts: datetime
    px: Decimal | None = None


@dataclass(frozen=True)
class ConvergenceEvent:
    """N ranked wallets converged on same coin + side within window. Triggers executor."""

    coin: str
    side: Side
    wallet_addresses: tuple[str, ...]
    opened_at_first: datetime
    opened_at_last: datetime
    score_floor_used: float
    window_seconds: int
    wallet_scores: tuple[float, ...] = field(default_factory=tuple)

    @property
    def wallet_count(self) -> int:
        return len(self.wallet_addresses)
