"""SL handler: Pro V3 forced exit."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..state import Store


def handle_sl(
    *, symbol: str, store: Store, blofin: BloFinClient,
    margin_usdt: float = 100.0, leverage: float = 30.0,
    initial_sl: float = 0.0, tp_ceiling: float = 0.0,
) -> dict[str, Any]:
    row = store.get_open_position(symbol)
    if row is None:
        return {"closed": False, "reason": "no open position"}

    # Cancel any pending SL algo first so the reduce-only close isn't blocked.
    try:
        blofin.cancel_all_tpsl(symbol)
    except Exception:
        pass
    if row.sl_order_id:
        store.record_sl_order_id(row.id, None)

    # Cancel any outstanding TP limit orders
    for stage, tp_id in (
        (1, row.tp1_order_id), (2, row.tp2_order_id), (3, row.tp3_order_id),
    ):
        if not tp_id:
            continue
        try:
            blofin.cancel_order(tp_id, symbol)
        except Exception:
            pass
        store.clear_tp_order_id(row.id, stage=stage)

    close_side = "sell" if row.side == "long" else "buy"
    fill = blofin.close_position_market(
        inst_id=symbol, side=close_side, contracts=row.current_size,
    )

    exit_price = fill["fill_price"]
    exit_reason = "trail_sl" if row.trail_active else "sl"

    store.log_trade(
        position_id=row.id, exit_price=exit_price, exit_reason=exit_reason,
        margin_usdt=margin_usdt, leverage=leverage,
        initial_sl=initial_sl, tp_ceiling=tp_ceiling,
    )
    store.close_position(row.id, realized_pnl=None)

    return {
        "closed": True,
        "exit_price": exit_price,
        "closed_contracts": row.current_size,
    }
