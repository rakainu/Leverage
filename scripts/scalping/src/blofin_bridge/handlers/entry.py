"""Entry handler: buy / sell with fixed-dollar SL and hard TP ceiling."""
from __future__ import annotations
import logging
from typing import Any, Optional

from ..blofin_client import BloFinClient
from ..sizing import (
    contracts_for_margin,
    SizingError,
)
from ..state import Store

log = logging.getLogger(__name__)


def _dollar_to_price_distance(dollar_amount: float, margin_usdt: float,
                               leverage: float, last_price: float) -> float:
    """Convert a dollar P&L amount to a price distance.

    notional = margin × leverage
    price_distance = (dollar_amount / notional) × last_price
    """
    notional = margin_usdt * leverage
    return (dollar_amount / notional) * last_price


def handle_entry(
    *,
    action: str,                           # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    # sizing
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
    # SL / trail / TP
    sl_loss_usdt: float,
    trail_activate_usdt: float,
    trail_distance_usdt: float,
    tp_limit_margin_pct: float,
) -> dict[str, Any]:
    """Open a new position with fixed-dollar SL and hard TP ceiling.

    SL distance derived from sl_loss_usdt:
      e.g. $100 margin × 30x = $3000 notional, $20 SL → 0.667% price move

    Hard TP ceiling placed as reduce-only limit at tp_limit_margin_pct of margin.
    Trailing stop logic is handled by the poller, not here.
    """
    existing = store.get_open_position(symbol)
    if existing is not None:
        return {
            "opened": False,
            "reason": f"position already open on {symbol} (id={existing.id})",
        }

    instrument = blofin.get_instrument(symbol)
    last_price = blofin.fetch_last_price(symbol)
    side: str = "long" if action == "buy" else "short"

    try:
        blofin.set_leverage(symbol, leverage=int(leverage), margin_mode=margin_mode)
    except Exception as exc:
        log.warning("set_leverage failed for %s @ %sx: %s", symbol, leverage, exc)

    # --- Compute SL price from fixed dollar loss ---
    sl_distance = _dollar_to_price_distance(sl_loss_usdt, margin_usdt, leverage, last_price)
    if side == "long":
        sl_trigger = last_price - sl_distance
    else:
        sl_trigger = last_price + sl_distance

    # --- Compute hard TP ceiling ---
    tp_dollar = margin_usdt * tp_limit_margin_pct
    tp_distance = _dollar_to_price_distance(tp_dollar, margin_usdt, leverage, last_price)
    if side == "long":
        tp_ceiling_price = last_price + tp_distance
    else:
        tp_ceiling_price = last_price - tp_distance

    # --- Size ---
    try:
        contracts = contracts_for_margin(
            margin_usdt=margin_usdt,
            leverage=leverage,
            last_price=last_price,
            instrument=instrument,
        )
    except SizingError as exc:
        return {"opened": False, "reason": f"sizing error: {exc}"}

    # --- Place entry with attached SL ---
    try:
        fill = blofin.place_market_entry(
            inst_id=symbol,
            side=action,
            contracts=contracts,
            safety_sl_trigger=sl_trigger,
        )
    except Exception as exc:
        log.exception("place_market_entry failed")
        return {"opened": False, "reason": f"entry placement failed: {exc}"}

    entry_price = fill.get("fill_price") or last_price

    # --- Persist position row ---
    pid = store.create_position(
        symbol=symbol, side=side, entry_price=entry_price,
        initial_size=contracts, sl_policy=sl_policy_name, source="pro_v3",
    )

    # --- Capture the tpslId of the SL that BloFin attached to the entry ---
    # create_order does not return the tpslId, so we fetch pending algos and
    # pick the one matching our trigger price. Record it so later cancel/replace
    # on trail promotions knows which order to sweep.
    sl_order_id: Optional[str] = None
    try:
        pending = blofin.list_pending_tpsl(symbol)
        best: Optional[dict[str, Any]] = None
        best_diff = float("inf")
        for algo in pending:
            tp_price = algo.get("slTriggerPrice")
            if not tp_price:
                continue
            diff = abs(float(tp_price) - sl_trigger)
            if diff < best_diff:
                best_diff = diff
                best = algo
        if best is not None:
            sl_order_id = best.get("tpslId") or None
            if sl_order_id:
                store.record_sl_order_id(pid, sl_order_id)
    except Exception:
        log.exception("capture attached sl tpslId failed")

    # --- Place hard TP ceiling as reduce-only limit ---
    tp_order_id: Optional[str] = None
    close_side = "sell" if side == "long" else "buy"
    try:
        tp_order_id = blofin.place_limit_reduce_only(
            inst_id=symbol, side=close_side,
            contracts=contracts, price=tp_ceiling_price,
        )
    except Exception as exc:
        log.exception("Hard TP ceiling placement failed")

    store.record_tp_order_ids(
        pid,
        tp1_order_id=tp_order_id,
        tp2_order_id=None,
        tp3_order_id=None,
    )

    return {
        "opened": True,
        "side": side,
        "position_id": pid,
        "entry_price": entry_price,
        "size": contracts,
        "sl_trigger": sl_trigger,
        "sl_order_id": sl_order_id,
        "sl_loss_usdt": sl_loss_usdt,
        "tp_ceiling_price": tp_ceiling_price,
        "tp_order_id": tp_order_id,
        "trail_activate_usdt": trail_activate_usdt,
        "trail_distance_usdt": trail_distance_usdt,
    }
