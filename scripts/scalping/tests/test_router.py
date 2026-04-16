import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.entry_gate import EntryGate
from blofin_bridge.router import dispatch, UnknownAction
from blofin_bridge.state import Store


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
    m.place_limit_reduce_only.return_value = "tp-ceiling-id"
    # 30 bars: enough for EMA(9) + slope + ATR(14)
    m.fetch_recent_ohlcv.return_value = [
        [1_700_000_000_000 + i * 300_000, 80.0, 81.0, 79.0, 80.0, 1000.0]
        for i in range(30)
    ]
    return m


@pytest.fixture
def cfg():
    return {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 30,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "sl_loss_usdt": 15, "trail_activate_usdt": 25,
            "trail_distance_usdt": 10, "tp_limit_margin_pct": 2.0,
            # --- snapshot config (flattened from Defaults in production) ---
            "ema_retest_period": 9,
            "ema_retest_timeframe": "5m",
            "ema_retest_timeout_minutes": 15,
            "atr_length": 14,
            "ema_slope_lookback": 1,
            "max_signal_age_seconds": 900,
            "max_signal_bars": 3,
        },
    }


def test_dispatch_buy_creates_pending_signal(store, blofin, cfg):
    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["pending"] is True
    assert result["action"] == "buy"
    # Verify signal was saved
    signals = store.list_pending_signals()
    assert len(signals) == 1
    assert signals[0]["symbol"] == "SOL-USDT"


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


def test_dispatch_tp_actions_are_unknown(store, blofin, cfg):
    """tp1/tp2/tp3 are no longer valid actions in the scalping router."""
    for action in ("tp1", "tp2", "tp3"):
        with pytest.raises(UnknownAction):
            dispatch(
                action=action, symbol="SOL-USDT",
                store=store, blofin=blofin, symbol_configs=cfg,
            )


def test_dispatch_returns_paused_when_gate_is_paused(store, blofin, cfg):
    """When EntryGate has the symbol paused, dispatch must NOT create a
    pending signal and must return a paused response."""
    gate = EntryGate(symbols=["SOL-USDT", "ZEC-USDT"])
    asyncio.new_event_loop().run_until_complete(gate.pause("SOL-USDT"))

    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg, gate=gate,
    )

    assert result == {
        "paused": True,
        "symbol": "SOL-USDT",
        "action": "buy",
        "reason": "entries paused by operator",
    }
    # No pending signal row should have been created.
    assert store.list_pending_signals() == []


def test_dispatch_sl_not_blocked_by_gate(store, blofin, cfg):
    """SL close actions are never blocked — operator safety."""
    gate = EntryGate(symbols=["SOL-USDT"])
    asyncio.new_event_loop().run_until_complete(gate.pause("SOL-USDT"))

    result = dispatch(
        action="sl", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg, gate=gate,
    )
    # With no open position, handle_sl returns {closed: False, reason: ...}
    # The key point: the response must NOT carry a "paused" flag.
    assert "paused" not in result


# --------------- snapshot capture ---------------

def _bars(num: int = 30, *, close: float = 100.0, high: float = 101.0, low: float = 99.0):
    return [[1_700_000_000_000 + i * 300_000, close, high, low, close, 1000.0]
            for i in range(num)]


def test_dispatch_buy_captures_snapshot_from_market(store, blofin, cfg):
    """When the webhook omits price/high/low, dispatch fetches them from market."""
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = _bars(30, close=100.0, high=101.0, low=99.0)

    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
    )
    assert result["pending"] is True

    signals = store.list_pending_signals()
    assert len(signals) == 1
    s = signals[0]
    assert s["signal_price"] == 100.0
    assert s["signal_candle_high"] == 101.0
    assert s["signal_candle_low"] == 99.0
    assert s["signal_ema_value"] is not None
    assert s["signal_ema_slope"] is not None
    assert s["signal_bar_ts"] is not None
    assert s["signal_timeframe"] == "5m"  # config default
    assert s["max_bars"] == 3
    assert s["max_age_seconds"] == 900


def test_dispatch_buy_uses_provided_payload_snapshot(store, blofin, cfg):
    """When the webhook includes price/high/low, dispatch uses those directly
    (does not overwrite from market ticker)."""
    blofin.fetch_last_price.return_value = 100.0
    blofin.fetch_recent_ohlcv.return_value = _bars(30, close=100.0, high=101.0, low=99.0)

    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store, blofin=blofin, symbol_configs=cfg,
        payload_price=104.25,
        payload_high=105.0,
        payload_low=103.5,
        payload_timeframe="5",
    )
    assert result["pending"] is True

    s = store.list_pending_signals()[0]
    assert s["signal_price"] == 104.25
    assert s["signal_candle_high"] == 105.0
    assert s["signal_candle_low"] == 103.5
    assert s["signal_timeframe"] == "5"


def test_dispatch_buy_snapshot_has_atr_when_bars_available(store, blofin, cfg):
    blofin.fetch_last_price.return_value = 100.0
    bars = [[1_700_000_000_000 + i * 300_000, 100.0, 100 + i * 0.1,
             99 - i * 0.1, 100.0, 1000.0] for i in range(30)]
    blofin.fetch_recent_ohlcv.return_value = bars

    dispatch(action="buy", symbol="SOL-USDT",
             store=store, blofin=blofin, symbol_configs=cfg)
    s = store.list_pending_signals()[0]
    assert s["signal_atr"] is not None
    assert s["signal_atr"] > 0
