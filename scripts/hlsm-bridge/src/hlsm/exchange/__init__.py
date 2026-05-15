"""Venue-pluggable exchange layer.

Concrete adapters:
- :class:`BloFinExchange` (ccxt) — MVP execution venue, demo + live via env flip.
- :class:`LighterStub` — V2 plug-in placeholder satisfying the interface contract.
"""
from hlsm.exchange.base import Exchange, ExchangeError
from hlsm.exchange.blofin import BloFinExchange
from hlsm.exchange.lighter import LighterStub
from hlsm.exchange.types import (
    Balance,
    OrderRequest,
    OrderResult,
    PerpInfo,
    PositionInfo,
    SLTPResult,
    Side,
)

__all__ = [
    "Exchange",
    "ExchangeError",
    "BloFinExchange",
    "LighterStub",
    "Balance",
    "OrderRequest",
    "OrderResult",
    "PerpInfo",
    "PositionInfo",
    "SLTPResult",
    "Side",
]
