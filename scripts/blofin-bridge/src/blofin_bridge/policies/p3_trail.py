"""P3 trail policy — STUB."""
from __future__ import annotations
from typing import Optional
from .base import Position, SLOrder


class P3Trail:
    def __init__(self, safety_sl_pct: float, trail_pct: float = 0.01) -> None:
        self.safety_sl_pct = safety_sl_pct
        self.trail_pct = trail_pct

    def on_entry(self, position: Position) -> SLOrder:
        raise NotImplementedError("P3 trail not implemented yet")

    def on_tp(self, position, tp_stage, tp_fill_price) -> Optional[SLOrder]:
        raise NotImplementedError("P3 trail not implemented yet")

    def on_tick(self, position, last_price) -> Optional[SLOrder]:
        raise NotImplementedError("P3 trail not implemented yet")
