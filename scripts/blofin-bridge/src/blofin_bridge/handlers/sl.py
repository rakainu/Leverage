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

    close_side = "sell" if row.side == "long" else "buy"
    fill = blofin.close_position_market(
        inst_id=symbol, side=close_side, contracts=row.current_size,
    )

    if row.sl_order_id:
        try:
            blofin.cancel_tpsl(symbol, row.sl_order_id)
        except Exception:
            pass  # If the SL order already triggered, cancel will fail; safe to ignore
        store.record_sl_order_id(row.id, None)

    store.close_position(row.id, realized_pnl=None)
    return {
        "closed": True,
        "exit_price": fill["fill_price"],
        "closed_contracts": row.current_size,
    }
