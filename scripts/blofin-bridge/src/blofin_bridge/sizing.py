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
    """Floor `raw` to the nearest `lot` multiple, absorbing FP noise.

    Adds a 1e-9 epsilon before the floor to handle cases like
    `0.3 * 3.0 = 0.8999...` which would otherwise drop a full lot.
    """
    if lot <= 0:
        return raw
    steps = math.floor(raw / lot + 1e-9)
    floored = steps * lot
    # Round to the lot's decimal precision to drop IEEE754 noise.
    decimals = max(0, -math.floor(math.log10(lot))) if lot < 1 else 0
    return round(floored, decimals)


def contracts_for_margin(
    *,
    margin_usdt: float,
    leverage: float,
    last_price: float,
    instrument: Instrument,
) -> float:
    """Return size in BloFin CONTRACTS for the given margin/leverage/price.

    ccxt's `amount` parameter for BloFin maps directly to BloFin's `size`
    field, which is the number of contracts. This is verified empirically:
    placing `amount=3.0` for ZEC produced a position of 3 contracts (0.3 ZEC),
    NOT 3 ZEC. So the bridge stores and passes contracts everywhere.

    For SOL (contractValue=1.0) contracts == base SOL coincidentally.
    For ZEC (contractValue=0.1) contracts == 10 × base ZEC, e.g. 30 contracts
    means 3 ZEC.
    """
    if leverage <= 0:
        raise SizingError("leverage must be positive")
    if margin_usdt <= 0:
        raise SizingError("margin_usdt must be positive")
    if last_price <= 0:
        raise SizingError("last_price must be positive")

    contract_value = instrument["contractValue"]
    notional = margin_usdt * leverage
    base_qty = notional / last_price                # e.g. 3.073 ZEC
    raw_contracts = base_qty / contract_value       # e.g. 30.73

    floored = _floor_to_lot(raw_contracts, instrument["lotSize"])

    if floored < instrument["minSize"]:
        raise SizingError(
            f"computed size {floored} contracts is below minSize "
            f"{instrument['minSize']}"
        )
    return floored


def close_fraction_to_contracts(
    open_contracts: float,
    fraction: float,
    instrument: Instrument,
) -> float:
    """Return contract count to close for a fractional TP (e.g. 0.40 for TP1).

    Input is in CONTRACTS (matching `contracts_for_margin`'s output).
    Output is also in contracts. Floors to lotSize.
    """
    if not 0 < fraction <= 1:
        raise SizingError("fraction must be in (0, 1]")
    raw = open_contracts * fraction
    floored = _floor_to_lot(raw, instrument["lotSize"])
    return max(0.0, floored)
