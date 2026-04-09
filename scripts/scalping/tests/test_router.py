from unittest.mock import MagicMock

import pytest

from blofin_bridge.router import dispatch, UnknownAction
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def blofin():
    m = MagicMock()
    m.get_instrument.return_value = {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }
    m.fetch_last_price.return_value = 80.0
    m.place_market_entry.return_value = {
        "orderId": "o1", "fill_price": 80.0, "filled": 12,
    }
    m.place_limit_reduce_only.return_value = "tp-ceiling-id"
    return m


@pytest.fixture
def cfg():
    return {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 30,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "sl_loss_usdt": 20, "trail_activate_usdt": 30,
            "trail_distance_usdt": 10, "tp_limit_margin_pct": 2.0,
        },
    }


def test_dispatch_buy_calls_entry_handler(store, blofin, cfg):
    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["opened"] is True


def test_dispatch_unknown_action_raises(store, blofin, cfg):
    with pytest.raises(UnknownAction):
        dispatch(
            action="wat", symbol="SOL-USDT",
            store=store, blofin=blofin, symbol_configs=cfg,
        )


def test_dispatch_disabled_symbol_rejected(store, blofin, cfg):
    cfg["SOL-USDT"]["enabled"] = False
    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["opened"] is False
    assert "disabled" in result["reason"].lower()


def test_dispatch_unknown_symbol_rejected(store, blofin, cfg):
    result = dispatch(
        action="buy", symbol="DOGE-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert "unknown symbol" in result["reason"].lower()


def test_dispatch_tp_actions_are_unknown(store, blofin, cfg):
    """tp1/tp2/tp3 are no longer valid actions in the scalping router."""
    for action in ("tp1", "tp2", "tp3"):
        with pytest.raises(UnknownAction):
            dispatch(
                action=action, symbol="SOL-USDT",
                store=store, blofin=blofin, symbol_configs=cfg,
            )
