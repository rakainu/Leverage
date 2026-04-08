from unittest.mock import MagicMock

import pytest

from blofin_bridge.blofin_client import BloFinClient, Instrument


@pytest.fixture
def mock_ccxt():
    m = MagicMock()
    m.load_markets.return_value = {
        "SOL/USDT:USDT": {
            "id": "SOL-USDT",
            "symbol": "SOL/USDT:USDT",
            "contractSize": 1.0,
            "limits": {"amount": {"min": 1.0}},
            "precision": {"amount": 1.0, "price": 0.001},
        }
    }
    return m


def test_client_loads_instruments(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    inst = client.get_instrument("SOL-USDT")
    assert isinstance(inst, dict)
    assert inst["instId"] == "SOL-USDT"
    assert inst["contractValue"] == 1.0
    assert inst["minSize"] == 1.0


def test_client_set_leverage_isolated_long(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.set_leverage("SOL-USDT", leverage=10, margin_mode="isolated")
    mock_ccxt.set_leverage.assert_called_once()
    args, kwargs = mock_ccxt.set_leverage.call_args
    assert args[0] == 10
    assert args[1] == "SOL/USDT:USDT"
    assert kwargs.get("params", {}).get("marginMode") == "isolated"


def test_get_unknown_instrument_raises(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    with pytest.raises(KeyError):
        client.get_instrument("DOGE-USDT")


def test_place_market_entry_with_attached_sl(mock_ccxt):
    mock_ccxt.create_order.return_value = {
        "id": "ord-1", "average": 80.12, "filled": 12,
    }
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    result = client.place_market_entry(
        inst_id="SOL-USDT", side="buy", contracts=12,
        safety_sl_trigger=76.0,
    )
    assert result["orderId"] == "ord-1"
    assert result["fill_price"] == 80.12

    mock_ccxt.create_order.assert_called_once()
    _, kwargs = mock_ccxt.create_order.call_args
    params = kwargs.get("params") or {}
    # Check that SL was attached
    assert params.get("slTriggerPrice") == 76.0
    assert params.get("slOrderPrice") == "-1"


def test_place_sl_order_returns_id(mock_ccxt):
    # ccxt doesn't have a dedicated tpsl method on BloFin; use privatePostTrade... style
    mock_ccxt.private_post_trade_order_tpsl = MagicMock(return_value={
        "code": "0",
        "data": [{"tpslId": "tpsl-42"}],
    })
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    tpsl_id = client.place_sl_order(
        inst_id="SOL-USDT", side="sell", trigger_price=80.0, margin_mode="isolated",
    )
    assert tpsl_id == "tpsl-42"


def test_cancel_tpsl_calls_correct_endpoint(mock_ccxt):
    mock_ccxt.private_post_trade_cancel_tpsl = MagicMock(return_value={"code": "0"})
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.cancel_tpsl("SOL-USDT", "tpsl-42")
    mock_ccxt.private_post_trade_cancel_tpsl.assert_called_once()


def test_close_position_market_uses_reduce_only(mock_ccxt):
    mock_ccxt.create_order.return_value = {"id": "close-1", "average": 83.5}
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.close_position_market(
        inst_id="SOL-USDT", side="sell", contracts=8,
    )
    _, kwargs = mock_ccxt.create_order.call_args
    assert kwargs["params"]["reduceOnly"] == "true"
