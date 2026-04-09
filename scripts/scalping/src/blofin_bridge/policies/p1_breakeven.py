"""P1 breakeven policy — STUB. Implement when needed."""
from __future__ import annotations
from typing import Optional
from .base import Position, SLOrder


class P1Breakeven:
    def __init__(self, safety_sl_pct: float) -> None:
        self.safety_sl_pct = safety_sl_pct

    def on_entry(self, position: Position) -> SLOrder:
        raise NotImplementedError("P1 breakeven not implemented yet")

    def on_tp(self, position, tp_stage, tp_fill_price) -> Optional[SLOrder]:
        raise NotImplementedError("P1 breakeven not implemented yet")

    def on_tick(self, position, last_price) -> Optional[SLOrder]:
        return None
