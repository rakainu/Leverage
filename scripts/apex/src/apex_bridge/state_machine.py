"""Apex 3-stage live trail-SL state machine.

Per open position, evaluated each tick:
  0 = initial  -> SL at entry -/+ (sl_loss_usdt / notional) * entry
  1 = BE       -> SL = entry              (after peak PnL >= breakeven_usdt)
  2 = trailing -> SL jumps to entry +/- ((trail_activate - trail_distance)/notional)*entry,
                  then trails the favorable extreme by trail_distance_usdt
                  (after peak PnL >= trail_activate_usdt)

At trail_activate $35 with trail_distance $15 the jump locks +$20, and the trail
keeps the SL $15 behind each new favorable extreme — never lowering it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import ExitConfig
from .executor import OpenPosition

log = logging.getLogger(__name__)


@dataclass
class StateMachineDecision:
    close: bool = False
    reason: str = ""


def _dollars_to_price_distance(usd: float, notional: float, ref_price: float) -> float:
    """Convert a $ amount of PnL into a price distance from ref_price."""
    if notional <= 0:
        return 0.0
    return (usd / notional) * ref_price


def _pnl_at(side: str, entry: float, price: float, base_amount: float) -> float:
    if side == "long":
        return (price - entry) * base_amount
    return (entry - price) * base_amount


def initial_sl(pos: OpenPosition, cfg: ExitConfig) -> float:
    """Compute the initial SL price at position open time."""
    sl_dist = _dollars_to_price_distance(cfg.sl_loss_usdt, pos.notional, pos.entry_price)
    return pos.entry_price - sl_dist if pos.side == "long" else pos.entry_price + sl_dist


def step(pos: OpenPosition, mark_price: float, cfg: ExitConfig) -> StateMachineDecision:
    """Evaluate one tick. Mutates `pos` (sl_price, state, trail_high) and returns
    a decision (whether to close, with reason)."""
    if pos.sl_price == 0.0:
        pos.sl_price = initial_sl(pos, cfg)
        log.info("%s: initial SL set @ $%.4f (entry=$%.4f, side=%s)",
                 pos.symbol, pos.sl_price, pos.entry_price, pos.side)

    # 1) Hard TP ceiling (favorable side) — a near-never safety cap.
    ceiling_pnl = pos.margin_usdt * cfg.tp_ceiling_pct
    pnl = _pnl_at(pos.side, pos.entry_price, mark_price, pos.base_amount)
    if pnl >= ceiling_pnl:
        return StateMachineDecision(close=True, reason="tp_ceiling")

    # 2) Update favorable extreme.
    better = (pos.side == "long" and mark_price > pos.trail_high) or \
             (pos.side == "short" and mark_price < pos.trail_high)
    if better:
        pos.trail_high = mark_price
        peak_pnl = pnl  # same float avoids subtraction-cancellation at near-entry prices
    else:
        peak_pnl = _pnl_at(pos.side, pos.entry_price, pos.trail_high, pos.base_amount)

    # 3) State advancement (sequential, so a single large tick can cascade 0->1->2).
    # Note: 1e-9 tolerance guards against float64 cancellation when pnl was computed
    # as (price - entry) * base, where subtraction of nearly-equal floats can lose ~7 ULP.
    _EPS = 1e-9
    if pos.state == 0 and peak_pnl >= cfg.breakeven_usdt - _EPS:
        pos.sl_price = pos.entry_price
        pos.state = 1
        log.info("%s: state 0->1 (BE). SL=$%.4f", pos.symbol, pos.sl_price)
    if pos.state == 1 and peak_pnl >= cfg.trail_activate_usdt - _EPS:
        jl = cfg.trail_activate_usdt - cfg.trail_distance_usdt   # locked profit at activation
        jd = _dollars_to_price_distance(jl, pos.notional, pos.entry_price)
        pos.sl_price = pos.entry_price + jd if pos.side == "long" else pos.entry_price - jd
        pos.state = 2
        log.info("%s: state 1->2 (TRAILING, locked $%.0f). SL=$%.4f",
                 pos.symbol, jl, pos.sl_price)

    # 4) If trailing, ratchet SL toward the favorable extreme (never lower it).
    # Use entry_price as the ref for dollar->price conversion (stable reference,
    # consistent with initial_sl and the state-jump computation).
    if pos.state == 2:
        td = _dollars_to_price_distance(cfg.trail_distance_usdt, pos.notional, pos.entry_price)
        new_sl = pos.trail_high - td if pos.side == "long" else pos.trail_high + td
        if pos.side == "long":
            pos.sl_price = max(pos.sl_price, new_sl)
        else:
            pos.sl_price = min(pos.sl_price, new_sl)

    pos.max_state = max(pos.max_state, pos.state)

    # 5) SL hit?
    sl_hit = (pos.side == "long" and mark_price <= pos.sl_price) or \
             (pos.side == "short" and mark_price >= pos.sl_price)
    if sl_hit:
        if pos.state >= 2:
            reason = "trail_sl"
        elif pos.state == 1:
            reason = "sl_be"
        else:
            reason = "sl"
        return StateMachineDecision(close=True, reason=reason)

    return StateMachineDecision(close=False)
