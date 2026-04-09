from unittest.mock import MagicMock

import pytest

from blofin_bridge.router import dispatch, UnknownAction
from blofin_bridge.state import Store
from blofin_bridge.policies.p2_step_stop import P2StepStop


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
    return m


@pytest.fixture
def cfg():
    return {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 10,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "safety_sl_pct": 0.05, "tp_split": [0.4, 0.3, 0.3],
            "atr_length": 3, "atr_timeframe": "5m",
            "sl_atr_multiplier": 3.0,
            "tp_atr_multipliers": [1.0, 2.0, 3.0],
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
