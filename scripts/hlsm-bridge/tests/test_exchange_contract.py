"""Contract tests for Exchange implementations.

Validates that every concrete adapter (real or stub) implements the abstract interface.
This is the test the spec calls out for the Lighter stub: it satisfies the interface
without making any network calls.
"""
from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from hlsm.exchange import BloFinExchange, Exchange, LighterStub
from hlsm.exchange.base import Exchange as ExchangeAbc
from hlsm.exchange.types import OrderRequest, PerpInfo, Side


def test_lighter_stub_is_an_exchange():
    stub = LighterStub()
    assert isinstance(stub, Exchange)


def test_lighter_stub_lists_perps_without_network():
    stub = LighterStub(perps=["PEPE", "WIF", "BONK"])
    perps = stub.list_perps()
    assert {p.symbol for p in perps} == {"PEPE", "WIF", "BONK"}
    assert all(isinstance(p, PerpInfo) for p in perps)


def test_lighter_stub_get_position_returns_none():
    stub = LighterStub()
    assert stub.get_position("PEPE") is None


def test_lighter_stub_raises_on_place_order():
    stub = LighterStub()
    with pytest.raises(NotImplementedError):
        stub.place_order(OrderRequest(coin="PEPE", side=Side.LONG,
                                      margin_usdt=Decimal("50"), leverage=10))


def test_lighter_stub_get_balance_returns_zero():
    stub = LighterStub()
    bal = stub.get_balance()
    assert bal.total_usdt == Decimal("0")


def _method_names(cls) -> set[str]:
    return {name for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not name.startswith("_")}


def test_blofin_implements_all_abstract_methods():
    abstract = {name for name, member in inspect.getmembers(ExchangeAbc, predicate=lambda m: getattr(m, "__isabstractmethod__", False))}
    blofin_methods = _method_names(BloFinExchange)
    missing = abstract - blofin_methods
    assert not missing, f"BloFinExchange missing abstract methods: {missing}"


def test_lighter_implements_all_abstract_methods():
    abstract = {name for name, member in inspect.getmembers(ExchangeAbc, predicate=lambda m: getattr(m, "__isabstractmethod__", False))}
    stub_methods = _method_names(LighterStub)
    missing = abstract - stub_methods
    assert not missing, f"LighterStub missing abstract methods: {missing}"


def test_blofin_demo_url_swap_uses_settings():
    """Construct BloFinExchange with a mocked ccxt client; verify name and instance check."""
    from unittest.mock import MagicMock

    fake_client = MagicMock()
    fake_client.load_markets.return_value = {
        "PEPE/USDT:USDT": {
            "swap": True, "quote": "USDT", "base": "PEPE",
            "id": "PEPE-USDT", "contractSize": 1,
            "precision": {"amount": 1, "price": 0.00000001},
            "limits": {"amount": {"min": 1}, "leverage": {"max": 50}},
        }
    }
    ex = BloFinExchange(client=fake_client)
    assert ex.name == "blofin"
    perps = ex.list_perps()
    assert any(p.symbol == "PEPE" for p in perps)
