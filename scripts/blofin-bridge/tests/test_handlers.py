from unittest.mock import MagicMock

import pytest

from blofin_bridge.handlers.entry import handle_entry
from blofin_bridge.policies.p2_step_stop import P2StepStop
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
    return m


def test_buy_opens_long_and_sets_safety_sl(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["opened"] is True
    assert result["side"] == "long"

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.side == "long"
    assert row.initial_size == 12
    assert row.entry_price == 80.12
    assert row.sl_order_id is None    # attached SL, not standalone

    blofin.place_market_entry.assert_called_once()
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["side"] == "buy"
    assert kwargs["contracts"] == 12
    # safety_sl_trigger = 80 * 0.95 = 76.0
    assert kwargs["safety_sl_trigger"] == pytest.approx(76.0, rel=1e-3)


def test_sell_opens_short_with_sl_above_entry(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    handle_entry(
        action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    row = store.get_open_position("SOL-USDT")
    assert row.side == "short"
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["side"] == "sell"
    assert kwargs["safety_sl_trigger"] == pytest.approx(84.0, rel=1e-3)


def test_entry_rejected_if_position_already_open(store, blofin):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=75.0,
        initial_size=5, sl_policy="p2_step_stop", source="pro_v3",
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_usdt=100, leverage=10, margin_mode="isolated",
        sl_policy_name="p2_step_stop",
    )
    assert result["opened"] is False
    assert "already open" in result["reason"].lower()
    blofin.place_market_entry.assert_not_called()


from blofin_bridge.handlers.tp import handle_tp


@pytest.fixture
def long_position_row(store):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=80.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "tpsl-initial")
    return store.get_position(pid)


def test_tp1_closes_40pct_and_sets_new_sl_at_entry(
    store, blofin, long_position_row
):
    policy = P2StepStop(safety_sl_pct=0.05)
    blofin.close_position_market.return_value = {
        "orderId": "close-1", "fill_price": 82.0,
    }
    blofin.place_sl_order.return_value = "tpsl-be"

    result = handle_tp(
        tp_stage=1, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated",
        tp_split=[0.40, 0.30, 0.30],
    )
    assert result["closed_contracts"] == 4
    assert result["new_sl_trigger"] == 80.0

    # Old SL cancelled
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "tpsl-initial")
    # New SL placed at entry (breakeven)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == 80.0

    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 1
    assert row.current_size == 8
    assert row.tp1_fill_price == 82.0
    assert row.sl_order_id == "tpsl-be"


def test_tp2_moves_sl_to_tp1_price(store, blofin, long_position_row):
    policy = P2StepStop(safety_sl_pct=0.05)
    # Simulate TP1 already happened
    store.record_tp_fill(long_position_row.id, stage=1, fill_price=82.0,
                         closed_contracts=4)
    store.record_sl_order_id(long_position_row.id, "tpsl-be")

    blofin.close_position_market.return_value = {
        "orderId": "close-2", "fill_price": 84.0,
    }
    blofin.place_sl_order.return_value = "tpsl-tp1"

    handle_tp(
        tp_stage=2, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    row = store.get_open_position("SOL-USDT")
    assert row.tp_stage == 2
    # 30% of ORIGINAL 12 = 3.6 -> floored to 3 contracts
    assert row.current_size == 5
    # New SL is at tp1 fill price (82.0)
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == 82.0


def test_tp3_closes_remainder_and_archives(store, blofin, long_position_row):
    policy = P2StepStop(safety_sl_pct=0.05)
    # Simulate through TP2
    store.record_tp_fill(long_position_row.id, stage=1, fill_price=82.0, closed_contracts=4)
    store.record_tp_fill(long_position_row.id, stage=2, fill_price=84.0, closed_contracts=3)
    store.record_sl_order_id(long_position_row.id, "tpsl-tp1")

    blofin.close_position_market.return_value = {
        "orderId": "close-3", "fill_price": 86.0,
    }

    handle_tp(
        tp_stage=3, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    # Position should be closed
    assert store.get_open_position("SOL-USDT") is None
    # SL cancelled
    blofin.cancel_tpsl.assert_called_once_with("SOL-USDT", "tpsl-tp1")
    # No new SL placed
    blofin.place_sl_order.assert_not_called()


def test_tp_discarded_when_no_open_position(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_tp(
        tp_stage=1, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    assert result["handled"] is False
    assert "no open position" in result["reason"].lower()
