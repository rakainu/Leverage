"""TP handler: tp1, tp2, tp3."""
from __future__ import annotations
import logging
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import Position, SLPolicy
from ..sizing import close_fraction_to_contracts
from ..state import Store

log = logging.getLogger(__name__)


def handle_tp(
    *,
    tp_stage: int,                # 1, 2, or 3
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_mode: str,
    tp_split: list[float],
) -> dict[str, Any]:
    if tp_stage not in (1, 2, 3):
        raise ValueError(f"invalid tp_stage {tp_stage}")

    row = store.get_open_position(symbol)
    if row is None:
        return {"handled": False, "reason": "no open position; stale tp alert"}

    # v1.1: idempotency — poller may have already advanced past this stage.
    if row.tp_stage >= tp_stage:
        return {
            "handled": True,
            "reason": f"tp{tp_stage} already processed (current tp_stage={row.tp_stage})",
            "idempotent": True,
        }

    # v1.1: cancel the still-pending BloFin limit order for this TP before
    # doing our own market close, otherwise BloFin's limit might still fill
    # and we'd double-close.
    tp_order_id = {
        1: row.tp1_order_id, 2: row.tp2_order_id, 3: row.tp3_order_id,
    }.get(tp_stage)
    if tp_order_id:
        try:
            blofin.cancel_order(tp_order_id, symbol)
        except Exception:
            pass  # may have already filled; safe to continue
        store.clear_tp_order_id(row.id, stage=tp_stage)

    instrument = blofin.get_instrument(symbol)

    # Fraction of ORIGINAL initial_size to close at this stage
    fraction = tp_split[tp_stage - 1]

    if tp_stage == 3:
        # Close entire remainder regardless of split math.
        to_close = row.current_size
    else:
        to_close = close_fraction_to_contracts(
            row.initial_size, fraction, instrument,
        )
        to_close = min(to_close, row.current_size)

    if to_close <= 0:
        return {"handled": False, "reason": "nothing to close (below lot size)"}

    close_side = "sell" if row.side == "long" else "buy"
    try:
        fill = blofin.close_position_market(
            inst_id=symbol, side=close_side, contracts=to_close,
        )
    except Exception as exc:
        if "102022" in str(exc):
            # Position was already closed on BloFin (e.g. SL triggered before this webhook)
            # Cancel any outstanding SL, archive the row, return success.
            if row.sl_order_id:
                try:
                    blofin.cancel_tpsl(symbol, row.sl_order_id)
                except Exception:
                    pass
                store.record_sl_order_id(row.id, None)
            store.close_position(row.id, realized_pnl=None)
            return {
                "handled": True, "tp_stage": tp_stage,
                "archived": True,
                "reason": "position already closed on BloFin (race with SL fire)",
            }
        raise

    # Cancel the current SL (and any orphan attached SL from entry OCO)
    blofin.cancel_all_tpsl(symbol)
    if row.sl_order_id:
        store.record_sl_order_id(row.id, None)

    store.record_tp_fill(
        row.id, stage=tp_stage, fill_price=fill["fill_price"],
        closed_contracts=to_close,
    )

    # Reload the updated row to pass to the policy
    updated = store.get_open_position(symbol) if tp_stage < 3 else row
    if tp_stage == 3 or (updated and updated.current_size == 0):
        store.close_position(row.id, realized_pnl=None)
        return {
            "handled": True, "tp_stage": tp_stage,
            "closed_contracts": to_close,
            "archived": True,
        }

    # Compute new SL via the policy
    pos_for_policy = Position(
        symbol=symbol, side=updated.side, entry_price=updated.entry_price,
        initial_size=updated.initial_size, current_size=updated.current_size,
        tp_stage=updated.tp_stage,
        tp1_fill_price=updated.tp1_fill_price,
        tp2_fill_price=updated.tp2_fill_price,
    )
    new_sl = policy.on_tp(pos_for_policy, tp_stage=tp_stage,
                          tp_fill_price=fill["fill_price"])
    if new_sl is None:
        return {
            "handled": True, "tp_stage": tp_stage,
            "closed_contracts": to_close, "new_sl_trigger": None,
        }

    try:
        new_sl_id = blofin.place_sl_order(
            inst_id=symbol, side=new_sl.side,
            trigger_price=new_sl.trigger_price, margin_mode=margin_mode,
        )
        store.record_sl_order_id(row.id, new_sl_id)
        return {
            "handled": True, "tp_stage": tp_stage,
            "closed_contracts": to_close,
            "new_sl_trigger": new_sl.trigger_price,
            "new_sl_id": new_sl_id,
        }
    except Exception as exc:
        if "102038" in str(exc):
            log.warning(
                "SL placement rejected by BloFin (trigger on wrong side of price): %s",
                exc,
            )
            # Leave sl_order_id null; partial close has already succeeded.
            return {
                "handled": True, "tp_stage": tp_stage,
                "closed_contracts": to_close,
                "new_sl_trigger": new_sl.trigger_price,
                "new_sl_id": None,
                "sl_placement_failed": True,
                "sl_placement_error": str(exc),
            }
        raise
