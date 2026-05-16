"""Lighter DEX stub.

A non-network placeholder that satisfies the :class:`Exchange` contract. Lets V2 swap-in
of a real Lighter SDK be a single-class change rather than a refactor. Contract test in
``tests/test_exchange_contract.py`` runs against this to prove the interface is implementable.
"""
from __future__ import annotations

from decimal import Decimal

from hlsm.exchange.base import Exchange
from hlsm.exchange.types import (
    Balance,
    OrderRequest,
    OrderResult,
    PerpInfo,
    PositionInfo,
    SLTPResult,
    Side,
)


class LighterStub(Exchange):
    """No-op Lighter adapter. Returns deterministic fixture data; raises on writes by design.

    When the V2 build wires the real lighter-python SDK in, this class is replaced by a
    LighterExchange that follows the BloFinExchange shape.
    """

    name = "lighter-stub"

    def __init__(self, *, perps: list[str] | None = None) -> None:
        self._perps = [p.upper() for p in (perps or ["PEPE", "WIF", "BONK"])]

    def list_perps(self) -> list[PerpInfo]:
        return [PerpInfo(symbol=c, venue_symbol=c, contract_value=Decimal("1")) for c in self._perps]

    def get_balance(self) -> Balance:
        # Stub: no real account state. Return zeros so the dashboard renders cleanly.
        return Balance(total_usdt=Decimal("0"), free_usdt=Decimal("0"), used_usdt=Decimal("0"))

    def get_position(self, coin: str) -> PositionInfo | None:  # noqa: ARG002
        return None

    def place_order(self, req: OrderRequest) -> OrderResult:  # noqa: ARG002
        raise NotImplementedError("LighterStub does not execute orders. Wire the real Lighter SDK in V2.")

    def attach_sl_tp(self, *, coin: str, side: Side, entry_px: Decimal,  # noqa: ARG002
                     sl_pct: Decimal, tp_pct: Decimal, size: Decimal,
                     leverage: int) -> SLTPResult:
        raise NotImplementedError("LighterStub does not place protective orders.")

    def close_position(self, *, coin: str, reason: str = "manual") -> OrderResult | None:  # noqa: ARG002
        return None
