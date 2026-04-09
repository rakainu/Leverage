"""P2 step-stop policy: hard SL -> breakeven on TP1 -> TP1-price on TP2."""
from __future__ import annotations
from typing import Optional

from .base import Position, SLOrder, SLPolicy


class P2StepStop:
    """Default v1 policy.

    - Entry: hard SL at safety_sl_pct from entry (5% default)
    - TP1 hit: move SL to entry price (breakeven)
    - TP2 hit: move SL to TP1 fill price (locks >= TP1 profit)
    - TP3 hit: no new SL (position fully closed by caller)
    """

    def __init__(self, safety_sl_pct: float) -> None:
        if not 0 < safety_sl_pct < 1:
            raise ValueError("safety_sl_pct must be in (0, 1)")
        self.safety_sl_pct = safety_sl_pct

    def on_entry(self, position: Position) -> SLOrder:
        if position.side == "long":
            trigger = position.entry_price * (1 - self.safety_sl_pct)
            closing_side = "sell"
        else:
            trigger = position.entry_price * (1 + self.safety_sl_pct)
            closing_side = "buy"
        return SLOrder(
            symbol=position.symbol,
            side=closing_side,
            trigger_price=round(trigger, 8),
            size=-1,
        )

    def on_tp(
        self,
        position: Position,
        tp_stage: int,
        tp_fill_price: float,
    ) -> Optional[SLOrder]:
        closing_side = "sell" if position.side == "long" else "buy"
        if tp_stage == 1:
            return SLOrder(
                symbol=position.symbol,
                side=closing_side,
                trigger_price=position.entry_price,
                size=-1,
            )
        if tp_stage == 2:
            if position.tp1_fill_price is None:
                raise ValueError("tp2 fired without a stored tp1_fill_price")
            return SLOrder(
                symbol=position.symbol,
                side=closing_side,
                trigger_price=position.tp1_fill_price,
                size=-1,
            )
        # TP3: position is fully closed by the handler, no SL to set.
        return None

    def on_tick(
        self,
        position: Position,
        last_price: float,
    ) -> Optional[SLOrder]:
        # P2 is event-driven (on_tp only). No per-tick updates.
        return None


# Type check: make sure P2StepStop satisfies SLPolicy
_: SLPolicy = P2StepStop(0.05)
