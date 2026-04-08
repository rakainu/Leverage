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
    # Default: ATR fetch raises so existing tests fall back to safety_sl_pct.
    m.fetch_recent_ohlcv.side_effect = Exception("no ohlcv mocked")
    return m


# === Entry handler tests (v1.1) ===


def _entry_kwargs(**overrides):
    defaults = dict(
        margin_usdt=100,
        leverage=10,
        margin_mode="isolated",
        sl_policy_name="p2_step_stop",
        atr_length=3,                      # use 3 for small test fixtures
        atr_timeframe="5m",
        sl_atr_multiplier=3.0,
        tp_atr_multipliers=[1.0, 2.0, 3.0],
        tp_split=[0.40, 0.30, 0.30],
        safety_sl_pct=0.05,
    )
    defaults.update(overrides)
    return defaults


def _mock_bars(bars_count: int = 6):
    """Return OHLCV bars where every TR = 1.0, so ATR(3) = 1.0."""
    out = []
    base = 100.0
    for i in range(bars_count):
        c = base + i * 0.0   # keep closes flat
        out.append([1_700_000_000 + i * 300, c, c + 0.5, c - 0.5, c, 1000.0])
    return out


def test_buy_opens_long_with_atr_based_sl_and_tps(store, blofin, sol_instrument):
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.side_effect = None
    blofin.fetch_recent_ohlcv.return_value = _mock_bars(6)
    blofin.place_market_entry.return_value = {
        "orderId": "e-1", "fill_price": 100.0, "filled": 12.5,
    }
    blofin.place_limit_reduce_only.side_effect = ["tp1-id", "tp2-id", "tp3-id"]

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["side"] == "long"
    assert result["atr_value"] == pytest.approx(1.0)
    assert result["sl_distance"] == pytest.approx(3.0)
    assert result["sl_trigger"] == pytest.approx(97.0)     # entry 100 - 3
    assert result["tp_prices"] == [pytest.approx(101.0), pytest.approx(102.0), pytest.approx(103.0)]

    # Entry placed with the ATR-based SL, not the 5% safety
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["safety_sl_trigger"] == pytest.approx(97.0)

    # 3 limit orders placed
    assert blofin.place_limit_reduce_only.call_count == 3
    call_prices = [c.kwargs["price"] for c in blofin.place_limit_reduce_only.call_args_list]
    assert call_prices == [pytest.approx(101.0), pytest.approx(102.0), pytest.approx(103.0)]
    call_sides = [c.kwargs["side"] for c in blofin.place_limit_reduce_only.call_args_list]
    assert call_sides == ["sell", "sell", "sell"]

    # Row persisted with all order ids and ATR context
    row = store.get_open_position("SOL-USDT")
    assert row.tp1_order_id == "tp1-id"
    assert row.tp2_order_id == "tp2-id"
    assert row.tp3_order_id == "tp3-id"
    assert row.atr_value == pytest.approx(1.0)
    assert row.sl_distance == pytest.approx(3.0)


def test_sell_opens_short_with_sl_above_and_tps_below(store, blofin, sol_instrument):
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.side_effect = None
    blofin.fetch_recent_ohlcv.return_value = _mock_bars(6)
    blofin.place_market_entry.return_value = {
        "orderId": "e-2", "fill_price": 100.0, "filled": 12.5,
    }
    blofin.place_limit_reduce_only.side_effect = ["tp1-id", "tp2-id", "tp3-id"]

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["side"] == "short"
    assert result["sl_trigger"] == pytest.approx(103.0)     # entry + 3
    assert result["tp_prices"] == [pytest.approx(99.0), pytest.approx(98.0), pytest.approx(97.0)]
    call_sides = [c.kwargs["side"] for c in blofin.place_limit_reduce_only.call_args_list]
    assert call_sides == ["buy", "buy", "buy"]


def test_entry_rejected_if_position_already_open(store, blofin, sol_instrument):
    store.create_position(
        symbol="SOL-USDT", side="long", entry_price=75.0,
        initial_size=5.0, sl_policy="p2_step_stop", source="pro_v3",
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["opened"] is False
    assert "already open" in result["reason"].lower()
    blofin.place_market_entry.assert_not_called()


def test_entry_falls_back_to_safety_sl_when_atr_fails(store, blofin, sol_instrument):
    """If fetch_recent_ohlcv raises, use the old safety_sl_pct path with no TPs."""
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 80.0
    blofin.fetch_recent_ohlcv.side_effect = Exception("ccxt boom")
    blofin.place_market_entry.return_value = {
        "orderId": "e-3", "fill_price": 80.0, "filled": 12.5,
    }

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["opened"] is True
    assert result["degraded"] is True
    assert result["atr_value"] is None
    # safety SL = 80 * 0.95 = 76
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["safety_sl_trigger"] == pytest.approx(76.0)
    # No TP orders placed
    blofin.place_limit_reduce_only.assert_not_called()


def test_entry_degraded_when_tp_placement_fails_midway(store, blofin, sol_instrument):
    """Entry succeeded, but TP2 placement raises. Position should still be saved + degraded."""
    blofin.get_instrument.return_value = sol_instrument
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.side_effect = None
    blofin.fetch_recent_ohlcv.return_value = _mock_bars(6)
    blofin.place_market_entry.return_value = {
        "orderId": "e-4", "fill_price": 100.0, "filled": 12.5,
    }
    # TP1 places OK, TP2 blows up, TP3 still attempted
    blofin.place_limit_reduce_only.side_effect = [
        "tp1-id", Exception("rate limit"), "tp3-id",
    ]

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    # Opened, but degraded
    assert result["opened"] is True
    assert result["degraded"] is True
    row = store.get_open_position("SOL-USDT")
    assert row is not None  # position row exists
    assert row.tp1_order_id == "tp1-id"
    # tp2 not saved
    assert row.tp2_order_id is None


def test_buy_opens_long_and_sets_safety_sl_fallback(store, blofin):
    """Legacy-style test: ATR mock raises, falls back to safety_sl_pct."""
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_entry(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
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


def test_sell_opens_short_with_sl_above_entry_fallback(store, blofin):
    policy = P2StepStop(safety_sl_pct=0.05)
    handle_entry(
        action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    row = store.get_open_position("SOL-USDT")
    assert row.side == "short"
    _, kwargs = blofin.place_market_entry.call_args
    assert kwargs["side"] == "sell"
    assert kwargs["safety_sl_trigger"] == pytest.approx(84.0, rel=1e-3)


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

    # Old SL cancelled (sweep mode)
    blofin.cancel_all_tpsl.assert_called_once_with("SOL-USDT")
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
    # SL cancelled (sweep mode)
    blofin.cancel_all_tpsl.assert_called_once_with("SOL-USDT")
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


from blofin_bridge.handlers.sl import handle_sl


def test_sl_force_closes_and_cancels_tpsl(store, blofin, long_position_row):
    blofin.close_position_market.return_value = {
        "orderId": "force-1", "fill_price": 78.0,
    }
    result = handle_sl(
        symbol="SOL-USDT", store=store, blofin=blofin,
    )
    assert result["closed"] is True
    assert store.get_open_position("SOL-USDT") is None
    blofin.cancel_all_tpsl.assert_called_once_with("SOL-USDT")
    _, kwargs = blofin.close_position_market.call_args
    assert kwargs["side"] == "sell"
    assert kwargs["contracts"] == 12


def test_sl_noop_when_flat(store, blofin):
    result = handle_sl(symbol="SOL-USDT", store=store, blofin=blofin)
    assert result["closed"] is False
    blofin.close_position_market.assert_not_called()


from blofin_bridge.handlers.reversal import handle_reversal


def test_reversal_buy_closes_short_and_opens_long(store, blofin):
    # Start with an open short
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=85.0,
        initial_size=12, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "tpsl-short")

    blofin.close_position_market.return_value = {
        "orderId": "close-1", "fill_price": 80.0,
    }
    blofin.place_market_entry.return_value = {
        "orderId": "open-1", "fill_price": 80.12, "filled": 12,
    }

    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_reversal(
        new_action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["closed_previous"] is True
    assert result["opened_new"] is True

    row = store.get_open_position("SOL-USDT")
    assert row.side == "long"
    # previous close happened AND a new entry happened
    assert blofin.cancel_all_tpsl.call_count == 1
    assert blofin.close_position_market.call_count == 1
    assert blofin.place_market_entry.call_count == 1


def test_reversal_with_no_prior_position_just_opens(store, blofin):
    blofin.place_market_entry.return_value = {
        "orderId": "open-1", "fill_price": 80.12, "filled": 12,
    }
    policy = P2StepStop(safety_sl_pct=0.05)
    result = handle_reversal(
        new_action="sell", symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        **_entry_kwargs(),
    )
    assert result["closed_previous"] is False
    assert result["opened_new"] is True
    blofin.close_position_market.assert_not_called()


def test_tp_handles_102022_race_with_sl_fire(store, blofin, long_position_row):
    """When BloFin has already closed the position (race with SL fire), tp handler archives cleanly."""
    policy = P2StepStop(safety_sl_pct=0.05)
    blofin.close_position_market.side_effect = Exception(
        "blofin {\"code\":\"1\",\"msg\":\"All operations failed\","
        "\"data\":[{\"code\":\"102022\",\"msg\":\"No positions on this contract.\"}]}"
    )
    result = handle_tp(
        tp_stage=3, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    assert result["handled"] is True
    assert result["archived"] is True
    assert "already closed" in result["reason"].lower()
    assert store.get_open_position("SOL-USDT") is None


def test_tp_handles_102038_sl_rejected(store, blofin, long_position_row):
    """When BloFin rejects the new SL placement (102038), partial close still counts as success with sl_placement_failed=True."""
    policy = P2StepStop(safety_sl_pct=0.05)
    blofin.close_position_market.return_value = {
        "orderId": "close-1", "fill_price": 84.0,
    }
    blofin.place_sl_order.side_effect = Exception(
        "blofin {\"code\":\"102038\",\"msg\":\"SL trigger price should be lower than the latest trading price\"}"
    )
    result = handle_tp(
        tp_stage=1, symbol="SOL-USDT",
        store=store, blofin=blofin, policy=policy,
        margin_mode="isolated", tp_split=[0.40, 0.30, 0.30],
    )
    assert result["handled"] is True
    assert result["sl_placement_failed"] is True
    assert "102038" in result["sl_placement_error"]
    # Position still open (partial close succeeded)
    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.tp_stage == 1
    assert row.sl_order_id is None  # No SL on record
