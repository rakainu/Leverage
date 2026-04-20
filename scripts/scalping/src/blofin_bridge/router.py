"""Action dispatch: webhook payload -> correct handler.

Bridge owns all exits — Pro V3 / any signal provider only OPENS setups.
Close / flip decisions live entirely in the bridge's SL state machine.
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .state import Store

if TYPE_CHECKING:
    from .entry_gate import EntryGate


class UnknownAction(ValueError):
    pass


VALID_ACTIONS = {"buy", "sell"}


def dispatch(
    *,
    action: str,
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    symbol_configs: dict[str, dict[str, Any]],
    gate: "EntryGate | None" = None,
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

    # Operator-initiated pause blocks new entries.
    if gate is not None and gate.is_paused(symbol):
        return {
            "paused": True,
            "symbol": symbol,
            "action": action,
            "reason": "entries paused by operator",
        }

    # Save as pending signal — poller will execute on EMA retest
    signal_price = blofin.fetch_last_price(symbol)
    # Cancel any existing pending signals for this symbol
    store.cancel_pending_signals_for_symbol(symbol)
    sig_id = store.create_pending_signal(
        symbol=symbol, action=action, signal_price=signal_price,
        timeout_minutes=sym_cfg.get("ema_retest_timeout_minutes", 30),
    )
    return {
        "pending": True,
        "signal_id": sig_id,
        "action": action,
        "signal_price": signal_price,
        "reason": "waiting for EMA retest",
    }
