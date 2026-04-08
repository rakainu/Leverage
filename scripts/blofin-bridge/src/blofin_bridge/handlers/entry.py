"""Entry handler: buy / sell."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import Position, SLPolicy
from ..sizing import contracts_for_margin, SizingError
from ..state import Store


def handle_entry(
    *,
    action: str,                    # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
) -> dict[str, Any]:
    """Open a new long (buy) or short (sell) position with attached safety SL."""
    existing = store.get_open_position(symbol)
    if existing is not None:
        return {
            "opened": False,
            "reason": f"position already open on {symbol} (id={existing.id})",
        }

    instrument = blofin.get_instrument(symbol)
    last_price = blofin.fetch_last_price(symbol)

    try:
        contracts = contracts_for_margin(
            margin_usdt=margin_usdt,
            leverage=leverage,
            last_price=last_price,
            instrument=instrument,
        )
    except SizingError as exc:
        return {"opened": False, "reason": f"sizing error: {exc}"}

    side: str = "long" if action == "buy" else "short"

    # Compute safety SL trigger from policy (uses last_price as entry proxy).
    proxy_position = Position(
        symbol=symbol, side=side, entry_price=last_price,
        initial_size=contracts, current_size=contracts,
        tp_stage=0, tp1_fill_price=None, tp2_fill_price=None,
    )
    sl_plan = policy.on_entry(proxy_position)

    # Place the entry with the attached SL in one call.
    fill = blofin.place_market_entry(
        inst_id=symbol,
        side=action,
        contracts=contracts,
        safety_sl_trigger=sl_plan.trigger_price,
    )

    pid = store.create_position(
        symbol=symbol, side=side, entry_price=fill["fill_price"],
        initial_size=contracts, sl_policy=sl_policy_name, source="pro_v3",
    )

    return {
        "opened": True,
        "side": side,
        "position_id": pid,
        "entry_price": fill["fill_price"],
        "size": contracts,
        "safety_sl_trigger": sl_plan.trigger_price,
    }
