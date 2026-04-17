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


def test_fetch_recent_ohlcv_rejects_non_ccxt_timeframe_with_clear_error(mock_ccxt):
    """Early-fail guard: catch bad timeframe values at our boundary with a
    clear ValueError instead of letting BloFin return opaque 152002
    'Parameter bar error'. Covers the bug where TV's bare-digit {{interval}}
    ("5" instead of "5m") reached this fetch and killed the whole pipeline.
    """
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    with pytest.raises(ValueError, match="timeframe"):
        client.fetch_recent_ohlcv("SOL-USDT", timeframe="5", limit=20)
    # ccxt.fetch_ohlcv must not be reached — we fail before the network call.
    mock_ccxt.fetch_ohlcv.assert_not_called()


def test_fetch_recent_ohlcv_accepts_valid_ccxt_timeframe(mock_ccxt):
    mock_ccxt.fetch_ohlcv.return_value = [[1, 2, 3, 4, 5, 6]]
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.fetch_recent_ohlcv("SOL-USDT", timeframe="5m", limit=20)
    mock_ccxt.fetch_ohlcv.assert_called_once()


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


def test_fetch_recent_ohlcv_returns_bars(mock_ccxt):
    mock_ccxt.fetch_ohlcv.return_value = [
        [1700000000000, 80.0, 80.5, 79.8, 80.2, 1000.0],
        [1700000300000, 80.2, 80.6, 80.1, 80.4, 1200.0],
        [1700000600000, 80.4, 80.9, 80.3, 80.7, 1500.0],
    ]
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    bars = client.fetch_recent_ohlcv("SOL-USDT", timeframe="5m", limit=20)
    assert len(bars) == 3
    assert bars[0][4] == 80.2  # close of first bar
    mock_ccxt.fetch_ohlcv.assert_called_once()
    args, kwargs = mock_ccxt.fetch_ohlcv.call_args
    assert args[0] == "SOL/USDT:USDT"    # ccxt symbol form
    assert kwargs.get("timeframe") == "5m" or (len(args) >= 2 and args[1] == "5m")
    assert kwargs.get("limit") == 20 or (len(args) >= 3 and args[2] == 20)


def test_place_limit_reduce_only_rounds_to_tick(mock_ccxt):
    mock_ccxt.create_order.return_value = {"id": "lim-1"}
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    # SOL tickSize is 0.001 per the mock fixture; price 80.1234 -> 80.123
    order_id = client.place_limit_reduce_only(
        inst_id="SOL-USDT", side="sell", contracts=5.0, price=80.1234,
    )
    assert order_id == "lim-1"
    mock_ccxt.create_order.assert_called_once()
    _, kwargs = mock_ccxt.create_order.call_args
    # price was rounded to tick 0.001
    assert kwargs.get("price") == pytest.approx(80.123)
    params = kwargs.get("params") or {}
    assert params.get("reduceOnly") == "true"
    assert params.get("marginMode") == "isolated"
    assert params.get("positionSide") == "net"


def test_place_limit_reduce_only_passes_contracts_amount(mock_ccxt):
    mock_ccxt.create_order.return_value = {"id": "lim-2"}
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.place_limit_reduce_only(
        inst_id="SOL-USDT", side="buy", contracts=3.75, price=83.5,
    )
    _, kwargs = mock_ccxt.create_order.call_args
    assert kwargs.get("amount") == 3.75
    assert kwargs.get("side") == "buy"
    assert kwargs.get("type") == "limit"


def test_place_limit_reduce_only_raises_on_missing_id(mock_ccxt):
    mock_ccxt.create_order.return_value = {}    # no id
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    with pytest.raises(RuntimeError, match="no order id"):
        client.place_limit_reduce_only(
            inst_id="SOL-USDT", side="sell", contracts=5.0, price=80.0,
        )


def test_fetch_order_delegates_to_ccxt(mock_ccxt):
    mock_ccxt.fetch_order.return_value = {"id": "ord-1", "status": "closed", "filled": 5.0}
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    result = client.fetch_order("ord-1", "SOL-USDT")
    assert result["status"] == "closed"
    mock_ccxt.fetch_order.assert_called_once_with("ord-1", "SOL/USDT:USDT")


def test_cancel_order_delegates_to_ccxt(mock_ccxt):
    mock_ccxt.cancel_order.return_value = None
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    client.cancel_order("ord-1", "SOL-USDT")
    mock_ccxt.cancel_order.assert_called_once_with("ord-1", "SOL/USDT:USDT")


def test_place_limit_reduce_only_rejects_non_positive_price(mock_ccxt):
    client = BloFinClient(ccxt_client=mock_ccxt)
    client.load_instruments()
    with pytest.raises(ValueError, match="price"):
        client.place_limit_reduce_only(
            inst_id="SOL-USDT", side="sell", contracts=5.0, price=0.0,
        )
