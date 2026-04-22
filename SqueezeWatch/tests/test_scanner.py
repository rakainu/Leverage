"""Unit tests for src/scanner.py — universe filtering."""

from unittest.mock import MagicMock

from src import scanner


def _make_exchange_info():
    return {
        "symbols": [
            {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
             "status": "TRADING", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
            {"symbol": "USDCUSDT", "baseAsset": "USDC", "quoteAsset": "USDT",
             "status": "TRADING", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
            {"symbol": "BTCBUSD", "baseAsset": "BTC", "quoteAsset": "BUSD",
             "status": "TRADING", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
            {"symbol": "DELISTEDUSDT", "baseAsset": "DEL", "quoteAsset": "USDT",
             "status": "BREAK", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
            {"symbol": "BTCUSDT_QUARTER", "baseAsset": "BTC", "quoteAsset": "USDT",
             "status": "TRADING", "contractType": "CURRENT_QUARTER",
             "onboardDate": 1569398400000},
            {"symbol": "DOGEUSDT", "baseAsset": "DOGE", "quoteAsset": "USDT",
             "status": "TRADING", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
            {"symbol": "BANNEDUSDT", "baseAsset": "BANNED", "quoteAsset": "USDT",
             "status": "TRADING", "contractType": "PERPETUAL",
             "onboardDate": 1569398400000},
        ]
    }


def test_fetch_universe_keeps_only_perpetual_usdt_trading():
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info()
    config = {"scanner": {"ban_list": ["BANNEDUSDT"]}}
    universe = scanner.fetch_universe(client, config)
    syms = {u["symbol"] for u in universe}
    assert "BTCUSDT" in syms
    assert "DOGEUSDT" in syms
    assert "USDCUSDT" not in syms       # stable base
    assert "BTCBUSD" not in syms        # not USDT quote
    assert "DELISTEDUSDT" not in syms   # not TRADING
    assert "BTCUSDT_QUARTER" not in syms  # not PERPETUAL
    assert "BANNEDUSDT" not in syms     # banlist


def test_fetch_universe_records_age_days():
    client = MagicMock()
    client.exchange_info.return_value = _make_exchange_info()
    universe = scanner.fetch_universe(client, {})
    btc = next(u for u in universe if u["symbol"] == "BTCUSDT")
    assert btc["age_days"] is not None and btc["age_days"] > 1500


def test_fetch_bulk_data_builds_maps():
    client = MagicMock()
    client.premium_index.return_value = [
        {"symbol": "BTCUSDT", "lastFundingRate": "-0.0001"},
        {"symbol": "ETHUSDT", "lastFundingRate": "0.0002"},
    ]
    client.ticker_24hr.return_value = [
        {"symbol": "BTCUSDT", "quoteVolume": "1000000000", "lastPrice": "50000"},
        {"symbol": "ETHUSDT", "quoteVolume": "500000000", "lastPrice": "3000"},
    ]
    funding, ticker = scanner.fetch_bulk_data(client)
    assert funding["BTCUSDT"] == -0.0001
    assert ticker["ETHUSDT"]["quote_volume_24h"] == 500_000_000
    assert ticker["ETHUSDT"]["price_last"] == 3000
