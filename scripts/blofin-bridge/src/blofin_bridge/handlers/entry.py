"""Entry handler: buy / sell with ATR-based SL and server-side TP orders (v1.1)."""
from __future__ import annotations
import logging
from typing import Any, Optional

from ..atr import wilders_atr, ATRError
from ..blofin_client import BloFinClient
from ..policies.base import SLPolicy
from ..sizing import (
    contracts_for_margin,
    close_fraction_to_contracts,
    SizingError,
)
from ..state import Store

log = logging.getLogger(__name__)


def handle_entry(
    *,
    action: str,                           # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    # sizing
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
    # v1.1 ATR
    atr_length: int,
    atr_timeframe: str,
    sl_atr_multiplier: float,
    tp_atr_multipliers: list[float],
    tp_split: list[float],
    safety_sl_pct: float,
) -> dict[str, Any]:
    """Open a new long (buy) or short (sell) position with ATR-based SL and TP orders.

    At entry:
      1. Fetch recent OHLCV and compute ATR(atr_length).
      2. SL distance  = atr × sl_atr_multiplier
         TP distances = atr × tp_atr_multipliers[i]  (3 values)
      3. Place market entry with attached SL at the computed price.
      4. Place 3 reduce-only limit orders at TP1/TP2/TP3 prices with tp_split sizes.
      5. Persist all order ids + ATR context in SQLite.

    If OHLCV fetch or ATR computation fails, fall back to the old safety_sl_pct
    path (attached SL only, no TP orders) and mark the position as degraded.
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

    # --- ATR path ---
    atr_value: Optional[float] = None
    sl_distance: Optional[float] = None
    tp_prices: list[float] = []
    degraded = False
    sl_trigger: float

    try:
        bars = blofin.fetch_recent_ohlcv(
            symbol, timeframe=atr_timeframe, limit=atr_length + 5,
        )
        atr_value = wilders_atr(bars, length=atr_length)
        sl_distance = atr_value * sl_atr_multiplier
        if side == "long":
            sl_trigger = last_price - sl_distance
            tp_prices = [last_price + atr_value * m for m in tp_atr_multipliers]
        else:
            sl_trigger = last_price + sl_distance
            tp_prices = [last_price - atr_value * m for m in tp_atr_multipliers]
    except Exception as exc:
        log.warning(
            "ATR computation failed for %s, falling back to safety_sl_pct: %s",
            symbol, exc,
        )
        degraded = True
        atr_value = None
        sl_distance = None
        tp_prices = []
        if side == "long":
            sl_trigger = last_price * (1 - safety_sl_pct)
        else:
            sl_trigger = last_price * (1 + safety_sl_pct)

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

    # --- Persist position row BEFORE attempting TPs, so we always have a record ---
    pid = store.create_position(
        symbol=symbol, side=side, entry_price=entry_price,
        initial_size=contracts, sl_policy=sl_policy_name, source="pro_v3",
    )
    if atr_value is not None:
        store.record_atr_context(
            pid, atr_value=atr_value, sl_distance=sl_distance or 0.0,
        )

    # --- Place TP limit orders (if ATR succeeded) ---
    tp_order_ids: list[Optional[str]] = [None, None, None]
    close_side = "sell" if side == "long" else "buy"
    tp_errors: list[str] = []

    if tp_prices:
        remaining_to_allocate = contracts
        for i, (tp_price, split_frac) in enumerate(zip(tp_prices, tp_split)):
            # TP3 (last) gets the REMAINDER (any lot-rounding loss goes here).
            if i == len(tp_prices) - 1:
                tp_size = remaining_to_allocate
            else:
                tp_size = close_fraction_to_contracts(
                    contracts, split_frac, instrument,
                )
                tp_size = min(tp_size, remaining_to_allocate)
                remaining_to_allocate -= tp_size

            if tp_size <= 0:
                tp_errors.append(f"TP{i+1} size rounded to 0")
                continue
            try:
                order_id = blofin.place_limit_reduce_only(
                    inst_id=symbol, side=close_side,
                    contracts=tp_size, price=tp_price,
                )
                tp_order_ids[i] = order_id
            except Exception as exc:
                log.exception("TP%d placement failed", i + 1)
                tp_errors.append(f"TP{i+1}: {exc}")
                degraded = True
                # Continue with remaining TPs; the position is already open
                # with SL, we just won't have all the TPs.

    store.record_tp_order_ids(
        pid,
        tp1_order_id=tp_order_ids[0],
        tp2_order_id=tp_order_ids[1],
        tp3_order_id=tp_order_ids[2],
    )

    result: dict[str, Any] = {
        "opened": True,
        "side": side,
        "position_id": pid,
        "entry_price": entry_price,
        "size": contracts,
        "sl_trigger": sl_trigger,
        "atr_value": atr_value,
        "sl_distance": sl_distance,
        "tp_prices": tp_prices if tp_prices else None,
        "tp_order_ids": tp_order_ids,
        "degraded": degraded,
    }
    if tp_errors:
        result["tp_errors"] = tp_errors
    return result
