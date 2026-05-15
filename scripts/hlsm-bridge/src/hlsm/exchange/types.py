"""Venue-agnostic data types. Adapters convert venue payloads to/from these."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class PerpInfo:
    """A single tradeable perpetual on the venue."""

    symbol: str          # canonical coin symbol e.g. "PEPE"
    venue_symbol: str    # venue-specific symbol e.g. "PEPE-USDT" for BloFin
    contract_value: Decimal = Decimal("1")
    min_size: Decimal = Decimal("0.01")
    lot_size: Decimal = Decimal("0.01")
    tick_size: Decimal = Decimal("0.00000001")
    max_leverage: int = 50


@dataclass(frozen=True)
class Balance:
    """Account balance snapshot. Currency is always USDT for perp accounts."""

    total_usdt: Decimal
    free_usdt: Decimal
    used_usdt: Decimal


@dataclass(frozen=True)
class OrderRequest:
    """Request to open a market position on a perp."""

    coin: str            # canonical symbol e.g. "PEPE"
    side: Side
    margin_usdt: Decimal
    leverage: int
    client_order_id: str | None = None


@dataclass(frozen=True)
class OrderResult:
    """Result of a market order. Contains exchange-side reference IDs."""

    order_id: str
    coin: str
    side: Side
    filled_size: Decimal       # in base units (e.g. PEPE)
    avg_fill_price: Decimal
    notional_usdt: Decimal
    fee_usdt: Decimal = Decimal("0")
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SLTPResult:
    """Result of attaching SL + TP. Each id is None if not placed."""

    sl_order_id: str | None
    tp_order_id: str | None
    sl_px: Decimal
    tp_px: Decimal


@dataclass(frozen=True)
class PositionInfo:
    """Current position state on the venue. None when flat."""

    coin: str
    side: Side
    size: Decimal               # base units, signed by side
    entry_px: Decimal
    mark_px: Decimal
    unrealized_pnl_usdt: Decimal
    leverage: int


CloseReason = Literal["sl", "tp", "wallet_exit", "breaker", "drain", "manual", "error"]
