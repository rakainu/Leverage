"""SL handler: Pro V3 forced exit."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..state import Store


def handle_sl(
    *, symbol: str, store: Store, blofin: BloFinClient,
) -> dict[str, Any]:
    row = store.get_open_position(symbol)
    if row is None:
        return {"closed": False, "reason": "no open position"}

    # Cancel any pending SL/TP first so the reduce-only close isn't blocked.
    try:
        blofin.cancel_all_tpsl(symbol)
    except Exception:
        pass
    if row.sl_order_id:
        store.record_sl_order_id(row.id, None)

    close_side = "sell" if row.side == "long" else "buy"
    fill = blofin.close_position_market(
        inst_id=symbol, side=close_side, contracts=row.current_size,
    )

    store.close_position(row.id, realized_pnl=None)
    return {
        "closed": True,
        "exit_price": fill["fill_price"],
        "closed_contracts": row.current_size,
    }
