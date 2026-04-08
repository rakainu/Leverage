"""Action dispatch: webhook payload -> correct handler."""
from __future__ import annotations
from typing import Any

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .handlers.tp import handle_tp
from .policies.p1_breakeven import P1Breakeven
from .policies.p2_step_stop import P2StepStop
from .policies.p3_trail import P3Trail
from .policies.p4_hybrid import P4Hybrid
from .state import Store


class UnknownAction(ValueError):
    pass


POLICY_REGISTRY = {
    "p1_breakeven": P1Breakeven,
    "p2_step_stop": P2StepStop,
    "p3_trail": P3Trail,
    "p4_hybrid": P4Hybrid,
}

VALID_ACTIONS = {
    "buy", "sell", "tp1", "tp2", "tp3", "sl",
    "reversal_buy", "reversal_sell",
}


def _build_policy(name: str, safety_sl_pct: float):
    cls = POLICY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown sl_policy {name}")
    return cls(safety_sl_pct=safety_sl_pct)


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

    policy = _build_policy(
        sym_cfg["sl_policy"], safety_sl_pct=sym_cfg["safety_sl_pct"],
    )

    if action in ("buy", "sell"):
        return handle_entry(
            action=action, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
        )

    if action in ("tp1", "tp2", "tp3"):
        stage = int(action[-1])
        return handle_tp(
            tp_stage=stage, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_mode=sym_cfg["margin_mode"],
            tp_split=sym_cfg["tp_split"],
        )

    if action == "sl":
        return handle_sl(symbol=symbol, store=store, blofin=blofin)

    if action.startswith("reversal_"):
        new_action = action.split("_", 1)[1]
        return handle_reversal(
            new_action=new_action, symbol=symbol, store=store, blofin=blofin,
            policy=policy, margin_usdt=sym_cfg["margin_usdt"],
            leverage=sym_cfg["leverage"], margin_mode=sym_cfg["margin_mode"],
            sl_policy_name=sym_cfg["sl_policy"],
        )

    raise UnknownAction(action)  # unreachable
