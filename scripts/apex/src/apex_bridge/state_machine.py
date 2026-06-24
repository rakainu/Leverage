"""Live trail-SL state machine. Real-time variant of sweeps strategy.simulate_trade.

Per open position, evaluated each tick:
  0 = initial  -> SL at entry - (sl_loss / notional) * entry
  1 = BE       -> SL = entry  (after profit >= breakeven_usdt)
  2 = lock     -> SL = entry + (lock_profit / notional) * entry  (after profit >= lock_profit_activate)
  3 = trail_set-> SL = entry + ((trail_start - trail_dist) / notional) * entry  (after profit >= trail_activate)
  4 = trailing -> SL trails the favorable extreme by trail_distance_usdt

On each tick we:
  - Update trail_high if a better favorable price was seen
  - Advance state if PnL hits the next threshold
  - If trailing, ratchet SL up (or down for shorts) toward trail_high
  - Check if current mark price has hit SL -> emit close
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
    """Evaluate one tick. Mutates `pos` (sl_price, state, trail_high)
    and returns a decision (whether to close, with reason).
    """
    if pos.sl_price == 0.0:
        pos.sl_price = initial_sl(pos, cfg)
        log.info("%s: initial SL set @ $%.4f (entry=$%.4f, side=%s)",
                 pos.symbol, pos.sl_price, pos.entry_price, pos.side)

    # 1) Check hard TP ceiling first (favorable side)
    ceiling_pnl = pos.margin_usdt * cfg.tp_ceiling_pct
    pnl = _pnl_at(pos.side, pos.entry_price, mark_price, pos.base_amount)
    if pnl >= ceiling_pnl:
        return StateMachineDecision(close=True, reason="tp_ceiling")

    # 2) Update trail_high if mark improved favorably
    better = (pos.side == "long" and mark_price > pos.trail_high) or \
             (pos.side == "short" and mark_price < pos.trail_high)
    if better:
        pos.trail_high = mark_price

    # Peak PnL based on trail_high (best price seen so far)
    peak_pnl = _pnl_at(pos.side, pos.entry_price, pos.trail_high, pos.base_amount)

    # 3) State advancement
    if pos.state == 0 and peak_pnl >= cfg.breakeven_usdt:
        pos.sl_price = pos.entry_price
        pos.state = 1
        log.info("%s: state 0->1 (BE). SL=$%.4f", pos.symbol, pos.sl_price)
    if pos.state == 1 and peak_pnl >= cfg.lock_profit_activate_usdt:
        ld = _dollars_to_price_distance(cfg.lock_profit_usdt, pos.notional, pos.entry_price)
        pos.sl_price = pos.entry_price + ld if pos.side == "long" else pos.entry_price - ld
        pos.state = 2
        log.info("%s: state 1->2 (lock $%.0f). SL=$%.4f",
                 pos.symbol, cfg.lock_profit_usdt, pos.sl_price)
    if pos.state == 2 and peak_pnl >= cfg.trail_activate_usdt:
        jl = cfg.trail_start_usdt - cfg.trail_distance_usdt
        jd = _dollars_to_price_distance(jl, pos.notional, pos.entry_price)
        pos.sl_price = pos.entry_price + jd if pos.side == "long" else pos.entry_price - jd
        pos.state = 3
        log.info("%s: state 2->3 (trail_set). SL=$%.4f", pos.symbol, pos.sl_price)
    if pos.state == 3 and peak_pnl >= cfg.trail_start_usdt:
        pos.state = 4
        log.info("%s: state 3->4 (TRAILING). trail_high=$%.4f", pos.symbol, pos.trail_high)

    # 4) If trailing, ratchet SL toward trail_high
    if pos.state == 4:
        td = _dollars_to_price_distance(cfg.trail_distance_usdt, pos.notional, pos.trail_high)
        new_sl = pos.trail_high - td if pos.side == "long" else pos.trail_high + td
        if pos.side == "long":
            pos.sl_price = max(pos.sl_price, new_sl)
        else:
            pos.sl_price = min(pos.sl_price, new_sl)

    pos.max_state = max(pos.max_state, pos.state)

    # 5) Check SL hit
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
