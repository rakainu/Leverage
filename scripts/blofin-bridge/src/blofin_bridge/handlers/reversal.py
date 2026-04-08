"""Reversal handler: close current + open opposite in one transition."""
from __future__ import annotations
from typing import Any

from ..blofin_client import BloFinClient
from ..policies.base import SLPolicy
from ..state import Store
from .entry import handle_entry
from .sl import handle_sl


def handle_reversal(
    *,
    new_action: str,                  # "buy" or "sell"
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    policy: SLPolicy,
    margin_usdt: float,
    leverage: float,
    margin_mode: str,
    sl_policy_name: str,
) -> dict[str, Any]:
    closed = handle_sl(symbol=symbol, store=store, blofin=blofin)
    opened = handle_entry(
        action=new_action, symbol=symbol,
        store=store, blofin=blofin, policy=policy,
        margin_usdt=margin_usdt, leverage=leverage,
        margin_mode=margin_mode, sl_policy_name=sl_policy_name,
    )
    return {
        "closed_previous": closed.get("closed", False),
        "opened_new": opened.get("opened", False),
        "close_result": closed,
        "open_result": opened,
    }
