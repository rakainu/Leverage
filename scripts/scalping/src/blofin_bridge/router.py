"""Action dispatch: webhook payload -> correct handler."""
from __future__ import annotations
from typing import Any

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .state import Store


class UnknownAction(ValueError):
    pass


VALID_ACTIONS = {
    "buy", "sell", "sl",
    "reversal_buy", "reversal_sell",
}


def dispatch(
    *,
    action: str,
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    symbol_configs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise UnknownAction(action)

    sym_cfg = symbol_configs.get(symbol)
    if sym_cfg is None:
        return {"opened": False, "handled": False,
                "reason": f"unknown symbol {symbol}"}
    if not sym_cfg.get("enabled", False):
        return {"opened": False, "handled": False,
                "reason": f"symbol {symbol} disabled in config"}

    if action in ("buy", "sell"):
        return handle_entry(
            action=action, symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
            sl_loss_usdt=sym_cfg["sl_loss_usdt"],
            trail_activate_usdt=sym_cfg["trail_activate_usdt"],
            trail_distance_usdt=sym_cfg["trail_distance_usdt"],
            tp_limit_margin_pct=sym_cfg["tp_limit_margin_pct"],
        )

    if action == "sl":
        return handle_sl(symbol=symbol, store=store, blofin=blofin)

    if action.startswith("reversal_"):
        new_action = action.split("_", 1)[1]
        return handle_reversal(
            new_action=new_action, symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
            sl_loss_usdt=sym_cfg["sl_loss_usdt"],
            trail_activate_usdt=sym_cfg["trail_activate_usdt"],
            trail_distance_usdt=sym_cfg["trail_distance_usdt"],
            tp_limit_margin_pct=sym_cfg["tp_limit_margin_pct"],
        )

    raise UnknownAction(action)  # unreachable
