"""Action dispatch: webhook payload -> correct handler."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .state import Store

if TYPE_CHECKING:
    from .entry_gate import EntryGate


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

    # Operator-initiated pause: block new entries but always allow SL (close-safe).
    entry_actions = ("buy", "sell", "reversal_buy", "reversal_sell")
    if gate is not None and action in entry_actions and gate.is_paused(symbol):
        return {
            "paused": True,
            "symbol": symbol,
            "action": action,
            "reason": "entries paused by operator",
        }

    if action in ("buy", "sell"):
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

    if action == "sl":
        # Also cancel any pending signals — we're closing
        store.cancel_pending_signals_for_symbol(symbol)
        return handle_sl(
            symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"], leverage=sym_cfg["leverage"],
        )

    if action.startswith("reversal_"):
        new_action = action.split("_", 1)[1]
        # Cancel pending, close existing, then queue new pending
        store.cancel_pending_signals_for_symbol(symbol)
        closed = handle_sl(
            symbol=symbol, store=store, blofin=blofin,
            margin_usdt=sym_cfg["margin_usdt"], leverage=sym_cfg["leverage"],
        )
        signal_price = blofin.fetch_last_price(symbol)
        sig_id = store.create_pending_signal(
            symbol=symbol, action=new_action, signal_price=signal_price,
            timeout_minutes=sym_cfg.get("ema_retest_timeout_minutes", 30),
        )
        return {
            "closed_previous": closed.get("closed", False),
            "pending_new": True,
            "signal_id": sig_id,
            "action": new_action,
            "signal_price": signal_price,
            "close_result": closed,
        }

    raise UnknownAction(action)  # unreachable
