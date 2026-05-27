"""Z-Fade exit machine — fixed ATR stop / ATR target, no trailing.

Z-Fade's exits are static: at entry, main.py sets pos.sl_price and pos.tp_price
from the signal bar's ATR (stop = entry -/+ sl_atr*ATR, target = entry +/- tp_atr*ATR).
This module just checks the live mark against those fixed levels every tick.

Mirrors sweeps/2026-05-20/strat_zscore.py's exit logic (SL on adverse extreme,
TP on favorable extreme). No breakeven / lock / trail — Z-Fade doesn't use them.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import ZFadeExits
from .executor import OpenPosition

log = logging.getLogger(__name__)


@dataclass
class StateMachineDecision:
    close: bool = False
    reason: str = ""


def step(pos: OpenPosition, mark_price: float, cfg: ZFadeExits) -> StateMachineDecision:
    """Evaluate one tick. Closes at the fixed ATR stop or target.

    sl_price / tp_price are set once at entry (see main.on_new_bar). If they are
    unset (0.0), the position is not yet armed — do nothing.
    """
    if pos.sl_price == 0.0 or pos.tp_price == 0.0:
        return StateMachineDecision(close=False)

    if pos.side == "long":
        if mark_price <= pos.sl_price:
            return StateMachineDecision(close=True, reason="sl")
        if mark_price >= pos.tp_price:
            return StateMachineDecision(close=True, reason="tp")
    else:  # short
        if mark_price >= pos.sl_price:
            return StateMachineDecision(close=True, reason="sl")
        if mark_price <= pos.tp_price:
            return StateMachineDecision(close=True, reason="tp")

    return StateMachineDecision(close=False)
