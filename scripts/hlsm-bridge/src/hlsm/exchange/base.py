"""Exchange interface. Every venue adapter MUST implement every method."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Iterable

from hlsm.exchange.types import (
    Balance,
    OrderRequest,
    OrderResult,
    PerpInfo,
    PositionInfo,
    SLTPResult,
)


class ExchangeError(RuntimeError):
    """Anything that prevents a venue operation from completing as intended."""


class Exchange(ABC):
    """Stable contract that the executor talks to. Concrete venues implement this."""

    name: str = "abstract"

    @abstractmethod
    def list_perps(self) -> list[PerpInfo]:
        """Return the full set of tradeable perp markets on the venue."""

    @abstractmethod
    def get_balance(self) -> Balance:
        """Return account-wide balance snapshot."""

    @abstractmethod
    def get_position(self, coin: str) -> PositionInfo | None:
        """Return current open position on the given coin, or None if flat."""

    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult:
        """Place a market order to open a position. Returns fill details."""

    @abstractmethod
    def attach_sl_tp(self, *, coin: str, side, entry_px: Decimal, sl_pct: Decimal, tp_pct: Decimal,
                     size: Decimal) -> SLTPResult:
        """Attach hard stop-loss and take-profit orders to an open position."""

    @abstractmethod
    def close_position(self, *, coin: str, reason: str = "manual") -> OrderResult | None:
        """Close the open position on `coin` at market. Returns None if already flat."""

    # Optional / for tests
    def cancel_protective_orders(self, *, coin: str) -> int:
        """Cancel SL + TP for a coin. Returns count cancelled. Default no-op for venues without TP/SL orders."""
        return 0


def venue_symbols_intersection(exchange: Exchange, desired_coins: Iterable[str]) -> list[str]:
    """Helper: given a list of canonical coin symbols, return the subset the venue actually supports."""
    venue_perps = {p.symbol.upper() for p in exchange.list_perps()}
    return sorted({c.upper() for c in desired_coins} & venue_perps)
