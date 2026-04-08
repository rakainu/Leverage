"""Position sizing math: margin & leverage -> BloFin contracts."""
from __future__ import annotations
import math
from typing import TypedDict


class Instrument(TypedDict):
    instId: str
    contractValue: float
    minSize: float
    lotSize: float
    tickSize: float


class SizingError(ValueError):
    """Raised when requested size cannot be fulfilled (below min, zero, etc)."""


def contracts_for_margin(
    *,
    margin_usdt: float,
    leverage: float,
    last_price: float,
    instrument: Instrument,
) -> int:
    """Return integer contract count for the given margin/leverage/price.

    Rounds DOWN to the nearest lot-size multiple so BloFin cannot reject for
    lot increment violations. Raises SizingError if the result is below
    the instrument's minSize.
    """
    if leverage <= 0:
        raise SizingError("leverage must be positive")
    if margin_usdt <= 0:
        raise SizingError("margin_usdt must be positive")
    if last_price <= 0:
        raise SizingError("last_price must be positive")

    notional = margin_usdt * leverage
    base_qty = notional / last_price                    # e.g. SOL count
    raw_contracts = base_qty / instrument["contractValue"]

    lot = instrument["lotSize"]
    floored = math.floor(raw_contracts / lot) * lot

    if floored < instrument["minSize"]:
        raise SizingError(
            f"computed size {floored} is below minSize {instrument['minSize']}"
        )
    return int(floored)


def close_fraction_to_contracts(
    open_contracts: int,
    fraction: float,
    instrument: Instrument,
) -> int:
    """Return contract count to close for a fractional TP (e.g. 0.40 for TP1).

    Rounds DOWN to lot size. Returns 0 if the result is below one lot —
    caller is responsible for handling the "nothing to close" case.
    """
    if not 0 < fraction <= 1:
        raise SizingError("fraction must be in (0, 1]")
    raw = open_contracts * fraction
    lot = instrument["lotSize"]
    floored = math.floor(raw / lot) * lot
    return int(max(0, floored))
