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
