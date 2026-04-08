"""Position sizing math: margin & leverage -> BloFin size."""
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


def _floor_to_lot(raw: float, lot: float) -> float:
    """Floor `raw` to the nearest `lot` multiple and round to avoid FP noise."""
    if lot <= 0:
        return raw
    steps = math.floor(raw / lot)
    floored = steps * lot
    # Round to the lot's decimal precision to drop IEEE754 noise like 12.500000001
    decimals = max(0, -math.floor(math.log10(lot))) if lot < 1 else 0
    return round(floored, decimals)


def contracts_for_margin(
    *,
    margin_usdt: float,
    leverage: float,
    last_price: float,
    instrument: Instrument,
) -> float:
    """Return size (in base units, e.g. SOL) for the given margin/leverage/price.

    Rounds DOWN to the nearest lot-size multiple so BloFin cannot reject for
    lot increment violations. Raises SizingError if the result is below the
    instrument's minSize.
    """
    if leverage <= 0:
        raise SizingError("leverage must be positive")
    if margin_usdt <= 0:
        raise SizingError("margin_usdt must be positive")
    if last_price <= 0:
        raise SizingError("last_price must be positive")

    notional = margin_usdt * leverage
    base_qty = notional / last_price
    raw = base_qty / instrument["contractValue"]

    floored = _floor_to_lot(raw, instrument["lotSize"])

    if floored < instrument["minSize"]:
        raise SizingError(
            f"computed size {floored} is below minSize {instrument['minSize']}"
        )
    return floored


def close_fraction_to_contracts(
    open_contracts: float,
    fraction: float,
    instrument: Instrument,
) -> float:
    """Return size to close for a fractional TP (e.g. 0.40 for TP1).

    Rounds DOWN to lot size. Returns 0 if the result is below one lot —
    caller is responsible for handling the "nothing to close" case.
    """
    if not 0 < fraction <= 1:
        raise SizingError("fraction must be in (0, 1]")
    raw = open_contracts * fraction
    floored = _floor_to_lot(raw, instrument["lotSize"])
    return max(0.0, floored)
