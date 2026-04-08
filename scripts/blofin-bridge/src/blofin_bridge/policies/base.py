"""SLPolicy interface and shared data types."""
from __future__ import annotations
from typing import NamedTuple, Optional, Protocol, Literal

Side = Literal["long", "short"]
OrderSide = Literal["buy", "sell"]


class Position(NamedTuple):
    symbol: str
    side: Side
    entry_price: float
    initial_size: float
    current_size: float
    tp_stage: int                         # 0, 1, 2, 3
    tp1_fill_price: Optional[float]
    tp2_fill_price: Optional[float]


class SLOrder(NamedTuple):
    symbol: str
    side: OrderSide                       # opposite of position side
    trigger_price: float
    size: float                           # -1 means "entire remaining position"


class SLPolicy(Protocol):
    def on_entry(self, position: Position) -> SLOrder: ...
    def on_tp(
        self,
        position: Position,
        tp_stage: int,
        tp_fill_price: float,
    ) -> Optional[SLOrder]: ...
    def on_tick(
        self,
        position: Position,
        last_price: float,
    ) -> Optional[SLOrder]: ...
