from unittest.mock import MagicMock

import pytest

from blofin_bridge.handlers.entry import handle_entry, _dollar_to_price_distance
from blofin_bridge.state import Store


@pytest.fixture
def sol_instrument():
    return {
        "instId": "SOL-USDT", "contractValue": 1.0, "minSize": 1.0,
        "lotSize": 1.0, "tickSize": 0.001,
    }


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "test.db")


@pytest.fixture
def blofin(sol_instrument):
    m = MagicMock()
    m.get_instrument.return_value = sol_instrument
    m.fetch_last_price.return_value = 80.0
    m.place_market_entry.return_value = {
        "orderId": "ord-1", "fill_price": 80.12, "filled": 12,
    }
    m.place_sl_order.return_value = "tpsl-1"
    m.place_limit_reduce_only.return_value = "tp-ceiling-id"
    # Default: no attached tpsl captured (tests that need the capture override).
    m.list_pending_tpsl.return_value = []
    return m


# === Helpers ===


def _entry_kwargs(**overrides):
    defaults = dict(
        margin_usdt=100,
        leverage=30,
        margin_mode="isolated",
        sl_policy_name="p2_step_stop",
        sl_loss_usdt=15,
        trail_activate_usdt=25,
        trail_distance_usdt=10,
        tp_limit_margin_pct=2.0,
    )
    defaults.update(overrides)
    return defaults


# === Dollar-to-price distance tests ===


def test_dollar_to_price_distance():
    # $100 margin, 30x = $3000 notional, $20 loss at price $300
    # distance = (20/3000) * 300 = 2.0
    dist = _dollar_to_price_distance(20, 100, 30, 300.0)
    assert dist == pytest.approx(2.0)


def test_dollar_to_price_distance_150_margin():
    # $150 margin, 30x = $4500 notional, $20 loss at price $300
    # distance = (20/4500) * 300 = 1.333
    dist = _dollar_to_price_distance(20, 150, 30, 300.0)
    assert dist == pytest.approx(1.3333, rel=1e-3)


# === Entry handler tests ===


def test_buy_opens_long_with_fixed_dollar_sl(store, blofin, sol_instrument):
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 300.0
    blofin.place_market_entry.return_value = {
        "orderId": "e-1", "fill_price": 300.0, "filled": 10,
    }

    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["side"] == "long"
    # SL: $15 loss on $100@30x=$3000 notional → (15/3000)*300 = 1.5
    assert result["sl_trigger"] == pytest.approx(298.5)
    # TP ceiling: 200% of $100 = $200 profit → (200/3000)*300 = 20.0
    assert result["tp_ceiling_price"] == pytest.approx(320.0)

    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["safety_sl_trigger"] == pytest.approx(298.5)

    # Hard TP ceiling placed
    blofin.place_limit_reduce_only.assert_called_once()
    _, tp_kwargs = blofin.place_limit_reduce_only.call_args
    assert tp_kwargs["price"] == pytest.approx(320.0)
    assert tp_kwargs["side"] == "sell"


def test_sell_opens_short_with_sl_above(store, blofin, sol_instrument):
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 300.0
    blofin.place_market_entry.return_value = {
        "orderId": "e-2", "fill_price": 300.0, "filled": 10,
    }

    result = handle_entry(
        action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["side"] == "short"
    # SL: 300 + (15/3000)*300 = 301.5
    assert result["sl_trigger"] == pytest.approx(301.5)
    assert result["tp_ceiling_price"] == pytest.approx(280.0)

    _, tp_kwargs = blofin.place_limit_reduce_only.call_args
    assert tp_kwargs["side"] == "buy"


def test_entry_rejected_if_position_already_open(store, blofin, sol_instrument):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=75.0,
        initial_size=5.0, sl_policy="p2_step_stop", source="pro_v3",
    )
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is False
    assert "already open" in result["reason"].lower()
    blofin.place_market_entry.assert_not_called()


def test_entry_persists_position_row(store, blofin):
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.side == "long"
    assert row.entry_price == 80.12
    assert row.trail_active == 0
    assert row.trail_high_price is None


def test_sl_adjusts_with_margin_size(store, blofin, sol_instrument):
    """$150 margin should give a tighter SL price distance."""
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 300.0
    blofin.place_market_entry.return_value = {
        "orderId": "e-3", "fill_price": 300.0, "filled": 15,
    }

    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(margin_usdt=150),
    )
    # $20 / ($150 × 30) × 300 = 1.333
    # $15 / ($150 × 30) × 300 = 1.0
    assert result["sl_trigger"] == pytest.approx(299.0, rel=1e-3)


# SL and reversal handlers removed — bridge owns all exits.


# === Attached SL tpslId capture ===


def test_entry_captures_attached_sl_tpsl_id(store, blofin, sol_instrument):
    """handle_entry must record the tpslId of the SL that BloFin attaches to
    the market entry, so subsequent trail promotions can cancel+replace by id."""
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 300.0
    blofin.place_market_entry.return_value = {
        "orderId": "e-1", "fill_price": 300.0, "filled": 10,
    }
    # Simulate BloFin's listing of pending tpsl post-entry. The handler picks
    # the one whose slTriggerPrice is closest to the computed sl_trigger.
    # $100 margin × 30x = $3000 notional; $15 loss at $300 → distance 1.5
    # Long side sl_trigger = 300 - 1.5 = 298.5
    blofin.list_pending_tpsl.return_value = [
        {"tpslId": "algo-captured", "slTriggerPrice": "298.5"},
    ]

    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["sl_order_id"] == "algo-captured"

    row = store.get_open_position("SOL-USDT")
    assert row.sl_order_id == "algo-captured"


def test_entry_handles_missing_attached_tpsl_gracefully(store, blofin, sol_instrument):
    """If BloFin's pending-tpsl listing returns empty (API blip, timing), the
    entry must still succeed. The SL is still live on the exchange — the only
    cost is we can't cancel it by id later (cancel_all_tpsl sweeps by symbol)."""
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 300.0
    blofin.place_market_entry.return_value = {
        "orderId": "e-2", "fill_price": 300.0, "filled": 10,
    }
    blofin.list_pending_tpsl.return_value = []

    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["sl_order_id"] is None
    row = store.get_open_position("SOL-USDT")
    assert row.sl_order_id is None
