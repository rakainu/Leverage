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
    """Return size in BASE UNITS (e.g. SOL, ZEC) for the given margin/leverage/price.

    Internally:
      1. Compute target notional in base currency: notional / last_price
      2. Convert to BloFin contracts: base_qty / contractValue
      3. Floor to instrument lotSize (which is expressed in contracts)
      4. Verify >= minSize (also in contracts)
      5. Return as base units: contracts × contractValue

    The bridge passes the returned value as ccxt's `amount` parameter, which
    ccxt expects in base currency. ccxt re-derives the BloFin contract count
    internally from the market metadata.

    For SOL-USDT (contractValue=1.0) base units == contracts so the math
    looks pass-through. For ZEC-USDT (contractValue=0.1) the conversion
    matters: 30 contracts = 3 ZEC.
    """
    if leverage <= 0:
        raise SizingError("leverage must be positive")
    if margin_usdt <= 0:
        raise SizingError("margin_usdt must be positive")
    if last_price <= 0:
        raise SizingError("last_price must be positive")

    contract_value = instrument["contractValue"]
    notional = margin_usdt * leverage
    base_qty = notional / last_price
    raw_contracts = base_qty / contract_value

    floored_contracts = _floor_to_lot(raw_contracts, instrument["lotSize"])

    if floored_contracts < instrument["minSize"]:
        raise SizingError(
            f"computed size {floored_contracts} contracts is below minSize "
            f"{instrument['minSize']}"
        )
    base_size = floored_contracts * contract_value
    # Round to contractValue's decimal precision to kill float noise.
    if contract_value < 1:
        decimals = max(0, -math.floor(math.log10(contract_value)))
        base_size = round(base_size, decimals + 4)
    return base_size


def close_fraction_to_contracts(
    open_size_base: float,
    fraction: float,
    instrument: Instrument,
) -> float:
    """Return size to close (in base units) for a fractional TP.

    Input `open_size_base` is the position size in base currency (what
    `contracts_for_margin` returns). Output is also in base units, ready
    to pass as ccxt's `amount`. Internally rounds to lotSize precision via
    contract conversion so BloFin won't reject the order.
    """
    if not 0 < fraction <= 1:
        raise SizingError("fraction must be in (0, 1]")
    contract_value = instrument["contractValue"]
    raw_base = open_size_base * fraction
    raw_contracts = raw_base / contract_value
    floored_contracts = _floor_to_lot(raw_contracts, instrument["lotSize"])
    base_size = max(0.0, floored_contracts * contract_value)
    if contract_value < 1 and base_size > 0:
        decimals = max(0, -math.floor(math.log10(contract_value)))
        base_size = round(base_size, decimals + 4)
    return base_size
