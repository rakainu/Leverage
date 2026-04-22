"""Unit tests for src/binance_client.py."""

from unittest.mock import patch, MagicMock

import pytest

from src.binance_client import BinanceFuturesClient


def _mock_response(status_code=200, json_data=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.headers = headers or {}
    r.json.return_value = json_data
    if status_code >= 400:
        r.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return r


def test_get_returns_json_on_success():
    client = BinanceFuturesClient()
    with patch.object(client.session, "get", return_value=_mock_response(200, {"ok": True})):
        result = client._get("/test")
    assert result == {"ok": True}


def test_get_retries_on_429_then_succeeds():
    client = BinanceFuturesClient(max_retries=2)
    responses = [
        _mock_response(429, headers={"Retry-After": "0"}),
        _mock_response(200, {"ok": True}),
    ]
    with patch.object(client.session, "get", side_effect=responses), \
         patch("src.binance_client.time.sleep"):
        result = client._get("/test")
    assert result == {"ok": True}


def test_get_retries_on_5xx_then_succeeds():
    client = BinanceFuturesClient(max_retries=2)
    responses = [
        _mock_response(503),
        _mock_response(200, {"ok": True}),
    ]
    with patch.object(client.session, "get", side_effect=responses), \
         patch("src.binance_client.time.sleep"):
        result = client._get("/test")
    assert result == {"ok": True}


def test_klines_passes_params():
    client = BinanceFuturesClient()
    with patch.object(client, "_get", return_value=[]) as g:
        client.klines("BTCUSDT", "1d", 30)
    g.assert_called_once_with(
        "/fapi/v1/klines",
        {"symbol": "BTCUSDT", "interval": "1d", "limit": 30},
    )


def test_open_interest_hist_passes_params():
    client = BinanceFuturesClient()
    with patch.object(client, "_get", return_value=[]) as g:
        client.open_interest_hist("BTCUSDT", "1d", 30)
    g.assert_called_once_with(
        "/futures/data/openInterestHist",
        {"symbol": "BTCUSDT", "period": "1d", "limit": 30},
    )


@pytest.mark.live
@pytest.mark.skip(reason="live smoke test — run manually: pytest -m live --no-skip")
def test_live_smoke_exchange_info():
    """Hit real Binance. Skipped in CI; run locally to verify network."""
    client = BinanceFuturesClient()
    info = client.exchange_info()
    assert "symbols" in info and len(info["symbols"]) > 100
