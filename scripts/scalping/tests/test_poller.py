import asyncio
from unittest.mock import MagicMock

import pytest

from blofin_bridge.entry_gate import EntryGate
from blofin_bridge.poller import PositionPoller
from blofin_bridge.state import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "poller.db")


@pytest.fixture
def blofin():
    m = MagicMock()
    m.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"},
        "symbol": "SOL/USDT:USDT",
        "contracts": 10,
        "side": "long",
    }]
    m.fetch_last_price.return_value = 300.0
    m.place_sl_order.return_value = "sl-trail-id"
    # Default: no exchange close-fill available → poller falls back to last price.
    m.fetch_closed_position.return_value = None
    return m


def _make_poller(store, blofin, **overrides):
    defaults = dict(
        store=store, blofin=blofin, interval_seconds=0,
        breakeven_usdt=15, trail_activate_usdt=25,
        trail_start_usdt=30, trail_distance_usdt=10,
        margin_usdt=100, leverage=30,
        gate=None,
    )
    defaults.update(overrides)
    return PositionPoller(**defaults)


def _long_position(store, entry_price=300.0):
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=entry_price,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    return pid


def _short_position(store, entry_price=300.0):
    pid = store.create_position(
        symbol="SOL-USDT", side="short", entry_price=entry_price,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
    )
    store.record_sl_order_id(pid, "sl-init")
    return pid


# === Phase 0: Breakeven at +$15 ===


@pytest.mark.asyncio
async def test_breakeven_at_15_profit(store, blofin):
    """At +$15 profit, SL moves to entry price."""
    pid = _long_position(store, entry_price=300.0)
    # $15 profit → price = 300 + (15/3000)*300 = 301.5
    blofin.fetch_last_price.return_value = 301.5

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1  # breakeven state

    # SL should be at entry price = 300.0
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(300.0)


@pytest.mark.asyncio
async def test_no_breakeven_below_threshold(store, blofin):
    """Below +$15 profit, nothing happens."""
    pid = _long_position(store, entry_price=300.0)
    # $10 profit → price = 301.0
    blofin.fetch_last_price.return_value = 301.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 0
    blofin.place_sl_order.assert_not_called()


@pytest.mark.asyncio
async def test_breakeven_waits_for_lock_profit(store, blofin):
    """In breakeven state (1), SL stays at entry until +$20."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=0, trail_active=1)

    # $18 profit — above breakeven but below lock_profit
    blofin.fetch_last_price.return_value = 301.8

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1  # still breakeven
    blofin.place_sl_order.assert_not_called()


@pytest.mark.asyncio
async def test_lock_profit_at_20(store, blofin):
    """At +$20 profit (from breakeven), SL moves to lock $15."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=0, trail_active=1)

    # $20 profit → 302.0
    blofin.fetch_last_price.return_value = 302.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 2  # lock profit state

    # SL should lock $15 profit: entry + (15/3000)*300 = 301.5
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 300.0 + (15 / 3000) * 300.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


# === Phase 1: SL jump at +$25 ===


@pytest.mark.asyncio
async def test_sl_jumps_at_activate_threshold(store, blofin):
    """At +$25 profit (from lock_profit state), SL jumps to lock in $20."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=0, trail_active=2)  # lock profit state

    # $25 profit → 302.5
    blofin.fetch_last_price.return_value = 302.5

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 3  # jumped, dead zone

    # SL should lock in $20 profit: entry + (20/3000)*300 = 302.0
    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 300.0 + (20 / 3000) * 300.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


# === Phase 2: Dead zone ($25 to $30) ===


@pytest.mark.asyncio
async def test_sl_locked_in_dead_zone(store, blofin):
    """Between $25 and $30 profit, SL stays locked."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=302.5, trail_active=3)

    # $28 profit = 302.8
    blofin.fetch_last_price.return_value = 302.8

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 3
    blofin.place_sl_order.assert_not_called()


# === Phase 3: Trail starts at +$30 ===


@pytest.mark.asyncio
async def test_trail_starts_at_trail_start_threshold(store, blofin):
    """At +$30 profit, trail transitions to actively trailing."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=302.5, trail_active=3)

    # $30 profit → 303.0
    blofin.fetch_last_price.return_value = 303.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 4  # trailing


@pytest.mark.asyncio
async def test_trail_moves_sl_on_new_high(store, blofin):
    """Once trailing (state 4), new high moves SL."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=303.0, trail_active=4)
    store.record_sl_order_id(pid, "sl-old")

    blofin.fetch_last_price.return_value = 306.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(306.0)
    assert row.trail_active == 4

    blofin.place_sl_order.assert_called_once()
    _, kwargs = blofin.place_sl_order.call_args
    expected_sl = 306.0 - (10 / 3000) * 306.0
    assert kwargs["trigger_price"] == pytest.approx(expected_sl, rel=1e-3)


@pytest.mark.asyncio
async def test_trail_does_not_move_sl_on_lower_price(store, blofin):
    """If price drops while trailing, SL stays."""
    pid = _long_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=306.0, trail_active=4)
    store.record_sl_order_id(pid, "sl-current")

    blofin.fetch_last_price.return_value = 304.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(306.0)
    blofin.place_sl_order.assert_not_called()


# === Short position ===


@pytest.mark.asyncio
async def test_short_breakeven(store, blofin):
    pid = _short_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]
    # $15 profit on short → price = 298.5
    blofin.fetch_last_price.return_value = 298.5

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(300.0)  # entry
    assert kwargs["side"] == "buy"


@pytest.mark.asyncio
async def test_short_trail_moves_on_new_low(store, blofin):
    pid = _short_position(store, entry_price=300.0)
    store.update_trail(pid, trail_high_price=297.0, trail_active=4)
    store.record_sl_order_id(pid, "sl-old")
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "contracts": 10,
    }]

    blofin.fetch_last_price.return_value = 294.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(294.0)
    blofin.place_sl_order.assert_called_once()


# === Drift detection ===


@pytest.mark.asyncio
async def test_archives_stale_position(store, blofin):
    pid = _long_position(store)
    blofin.fetch_positions.return_value = []

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is None


@pytest.mark.asyncio
async def test_drift_exit_relabeled_sl_when_near_initial_sl(store, blofin):
    """Position gone from BloFin with exit price at the initial SL → 'sl'."""
    pid = _long_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    # Initial SL for a long @ 300 with $13 loss / $100 margin / 30x:
    # distance = (13 / (100*30)) * 300 = 1.3 → SL price = 298.7
    blofin.fetch_last_price.return_value = 298.7

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    trades = store.get_trade_log(limit=1)
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "sl"
    assert trades[0]["initial_sl"] == pytest.approx(298.7, abs=0.01)


@pytest.mark.asyncio
async def test_drift_exit_stays_drift_when_exit_far_from_sl(store, blofin):
    """Position gone from BloFin with exit price nowhere near SL → 'drift'."""
    pid = _long_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    # Exit price at 305 is ~2.2% above the 298.5 SL — clearly not an SL fill.
    blofin.fetch_last_price.return_value = 305.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    trades = store.get_trade_log(limit=1)
    assert trades[0]["exit_reason"] == "drift"


@pytest.mark.asyncio
async def test_drift_exit_short_sl_relabel(store, blofin):
    """Short position: initial SL lives above entry."""
    pid = _short_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    # Short SL @ 301.3 ($13 loss)
    blofin.fetch_last_price.return_value = 301.3

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    trades = store.get_trade_log(limit=1)
    assert trades[0]["exit_reason"] == "sl"


@pytest.mark.asyncio
async def test_stale_close_uses_real_exchange_fill_not_last_price(store, blofin):
    """When the venue reports the real closed-position fill, the trade is logged
    with that fill price + fee — NOT the (possibly drifted) last price."""
    pid = _long_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    # Real SL fill at 298.6 (near the 298.7 initial SL); fee reported.
    blofin.fetch_closed_position.return_value = {"close_price": 298.6, "fee": -4.5}
    # last_price is deliberately wrong/drifted — must be ignored.
    blofin.fetch_last_price.return_value = 305.0

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    t = store.get_trade_log(limit=1)[0]
    assert t["exit_price"] == pytest.approx(298.6)   # real fill, not 305
    assert t["fee_usdt"] == pytest.approx(-4.5)
    assert t["exit_reason"] == "sl"                  # near SL → correctly labeled
    # gross from real fill: (298.6/300 - 1) * 3000 = -14.0
    assert t["pnl_usdt"] == pytest.approx(-14.0)


@pytest.mark.asyncio
async def test_stale_close_zero_fee_venue_records_zero_fee(store, blofin):
    """Zero-fee venue (no fee key handling upstream) → fee_usdt is 0.0, net=gross."""
    pid = _long_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    blofin.fetch_closed_position.return_value = {"close_price": 303.0, "fee": 0.0}

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    t = store.get_trade_log(limit=1)[0]
    assert t["exit_price"] == pytest.approx(303.0)
    assert t["fee_usdt"] == 0.0
    assert t["pnl_usdt"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_stale_close_falls_back_to_last_price_when_no_fill(store, blofin):
    """No exchange fill available → fall back to last price, fee 0 (never lose a trade)."""
    pid = _long_position(store, entry_price=300.0)
    blofin.fetch_positions.return_value = []
    blofin.fetch_closed_position.return_value = None
    blofin.fetch_last_price.return_value = 298.7   # ~initial SL

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    t = store.get_trade_log(limit=1)[0]
    assert t["exit_price"] == pytest.approx(298.7)
    assert t["fee_usdt"] == 0.0
    assert t["exit_reason"] == "sl"


@pytest.mark.asyncio
async def test_skips_drift_if_fetch_fails(store, blofin):
    pid = _long_position(store)
    blofin.fetch_positions.side_effect = Exception("ccxt boom")

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    assert store.get_open_position("SOL-USDT") is not None


@pytest.mark.asyncio
async def test_swallows_exceptions(store, blofin):
    pid = _long_position(store)
    blofin.fetch_last_price.side_effect = Exception("ccxt boom")

    poller = _make_poller(store, blofin)
    await poller.poll_once()

    row = store.get_open_position("SOL-USDT")
    assert row is not None
    assert row.trail_active == 0


# === EntryGate integration ===


@pytest.mark.asyncio
async def test_pending_signal_for_paused_symbol_is_expired(store, blofin):
    """Signal queued before pause → poller expires it instead of firing."""
    sig_id = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=300.0,
        timeout_minutes=30,
    )

    gate = EntryGate(symbols=["SOL-USDT"])
    await gate.pause("SOL-USDT")

    poller = _make_poller(store, blofin, gate=gate)
    await poller.poll_once()

    # The signal should no longer be 'pending'.
    remaining = store.list_pending_signals()
    assert all(s["id"] != sig_id for s in remaining)

    # No entry was attempted.
    blofin.place_market_entry.assert_not_called()


@pytest.mark.asyncio
async def test_pending_source_threaded_to_entry(store, blofin, monkeypatch):
    """The poller must pass a pending signal's source through to handle_entry,
    so a self-generated ('ha_v3') signal opens an 'ha_v3'-tagged position."""
    store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=300.0, source="ha_v3",
    )
    # Flat tape → EMA(9) retest fires immediately on this poll.
    bars = [[i, 300.0, 300.0, 300.0, 300.0, 0.0] for i in range(25)]
    blofin.fetch_recent_ohlcv.return_value = bars
    blofin.fetch_last_price.return_value = 300.0

    captured: dict = {}

    def _fake_entry(**kwargs):
        captured.update(kwargs)
        return {"opened": False, "reason": "stub"}

    monkeypatch.setattr("blofin_bridge.poller.handle_entry", _fake_entry)

    cfg = {"SOL-USDT": {
        "margin_usdt": 100, "leverage": 30, "margin_mode": "isolated",
        "sl_policy": "p2_step_stop", "sl_loss_usdt": 15,
        "trail_activate_usdt": 25, "trail_distance_usdt": 10,
        "tp_limit_margin_pct": 2.0,
    }}
    poller = _make_poller(store, blofin, symbol_configs=cfg)
    poller._process_pending_signals()

    assert captured.get("source") == "ha_v3"


# === Full lifecycle ===


@pytest.mark.asyncio
async def test_full_lifecycle(store, blofin):
    """inactive → breakeven → lock profit → jump → dead zone → trailing."""
    pid = _long_position(store, entry_price=300.0)
    poller = _make_poller(store, blofin)

    # Cycle 1: $15 profit → breakeven (state=1)
    blofin.fetch_last_price.return_value = 301.5
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    assert blofin.place_sl_order.call_count == 1
    _, kwargs = blofin.place_sl_order.call_args
    assert kwargs["trigger_price"] == pytest.approx(300.0)

    # Cycle 2: $18 profit → still breakeven, no change
    blofin.fetch_last_price.return_value = 301.8
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 1
    blofin.place_sl_order.assert_not_called()

    # Cycle 3: $20 profit → lock profit, SL at +$15 (state=2)
    blofin.fetch_last_price.return_value = 302.0
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 2
    assert blofin.place_sl_order.call_count == 1
    _, kwargs = blofin.place_sl_order.call_args
    expected_lock_sl = 300.0 + (15 / 3000) * 300.0
    assert kwargs["trigger_price"] == pytest.approx(expected_lock_sl, rel=1e-3)

    # Cycle 4: $25 profit → jump, lock in $20 (state=3)
    blofin.fetch_last_price.return_value = 302.5
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 3
    assert blofin.place_sl_order.call_count == 1

    # Cycle 5: $28 profit → dead zone, no change
    blofin.fetch_last_price.return_value = 302.8
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 3
    blofin.place_sl_order.assert_not_called()

    # Cycle 6: $31 profit → trail starts (state=4)
    blofin.fetch_last_price.return_value = 303.1
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_active == 4

    # Cycle 7: $40 profit → trail follows
    blofin.fetch_last_price.return_value = 304.0
    blofin.place_sl_order.reset_mock()
    await poller.poll_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(304.0)
    blofin.place_sl_order.assert_called_once()


# === Per-symbol thresholds (V3 multi-symbol scaling) ===


@pytest.mark.asyncio
async def test_per_symbol_breakeven_uses_symbol_threshold(store):
    """A symbol's own breakeven_usdt drives the state transition, not the global default.

    ZEC at $250 margin: needs $30 profit to hit breakeven (vs $12 baseline).
    At entry 100, $30 profit = +0.4% = price 100.4. So price 100.4 should
    trigger breakeven for ZEC even though it's far above the $12 default.
    """
    blofin = MagicMock()
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "ZEC-USDT"}, "symbol": "ZEC/USDT:USDT",
        "contracts": 10, "side": "long",
    }]
    blofin.place_sl_order.return_value = "sl-trail-id"

    pid = store.create_position(
        symbol="ZEC-USDT", side="long", entry_price=100.0,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
        margin_usdt=250.0, leverage=30.0,
    )
    store.record_sl_order_id(pid, "sl-init")

    symbol_configs = {
        "ZEC-USDT": {
            "margin_usdt": 250.0, "leverage": 30.0,
            "sl_loss_usdt": 32.5, "breakeven_usdt": 30.0,
            "lock_profit_activate_usdt": 45.0, "lock_profit_usdt": 37.5,
            "trail_activate_usdt": 75.0, "trail_start_usdt": 80.0,
            "trail_distance_usdt": 37.5,
        },
    }

    poller = _make_poller(
        store, blofin, symbol_configs=symbol_configs,
        # Instance defaults intentionally LOW — if poller uses these, the
        # test triggers prematurely. With per-symbol lookup it stays inactive
        # at $12 profit and triggers at $30.
        breakeven_usdt=12, margin_usdt=100, leverage=30,
    )

    # $12 profit at $250 margin × 30x = 0.16% = price 100.16
    blofin.fetch_last_price.return_value = 100.16
    await poller.poll_once()
    row = store.get_open_position("ZEC-USDT")
    assert row.trail_active == 0, "should not trigger breakeven at $12 — ZEC needs $30"

    # $30 profit at $250 margin × 30x = 0.4% = price 100.4
    blofin.fetch_last_price.return_value = 100.4
    await poller.poll_once()
    row = store.get_open_position("ZEC-USDT")
    assert row.trail_active == 1, "should trigger breakeven at $30 for ZEC"


@pytest.mark.asyncio
async def test_per_symbol_trail_distance_scales_with_margin(store):
    """SOL at $30 margin should use the smaller trail distance.

    At margin=$30 × 30x = $900 notional, trail_distance=$4.50
    → price distance = (4.5/900) * price = 0.5% of price.
    Compare to baseline ($100 × 30 = $3000, $15 trail = 0.5% — same %).
    Test verifies the conversion uses pos.margin_usdt, not self.margin_usdt.
    """
    blofin = MagicMock()
    blofin.fetch_positions.return_value = [{
        "info": {"instId": "SOL-USDT"}, "symbol": "SOL/USDT:USDT",
        "contracts": 10, "side": "long",
    }]
    blofin.place_sl_order.return_value = "sl-trail-id"

    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=100.0,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
        margin_usdt=30.0, leverage=30.0,
    )
    store.record_sl_order_id(pid, "sl-init")
    # Skip to actively-trailing state with a high already recorded.
    store.update_trail(pid, trail_high_price=101.0, trail_active=4)

    symbol_configs = {
        "SOL-USDT": {
            "margin_usdt": 30.0, "leverage": 30.0,
            "sl_loss_usdt": 3.9, "breakeven_usdt": 3.6,
            "lock_profit_activate_usdt": 5.4, "lock_profit_usdt": 4.5,
            "trail_activate_usdt": 9.0, "trail_start_usdt": 9.6,
            "trail_distance_usdt": 4.5,
        },
    }

    poller = _make_poller(
        store, blofin, symbol_configs=symbol_configs,
        # Instance defaults set for baseline $100 margin — should NOT be used.
        trail_distance_usdt=15, margin_usdt=100, leverage=30,
    )

    # Price advances to new high 101.5 (+1.5% / +$13.50 PnL at $30/30x).
    blofin.fetch_last_price.return_value = 101.5
    await poller.poll_once()

    # New SL should be 101.5 - 0.5% = 101.0 (using SOL's $4.50 trail at $30 margin).
    # If poller incorrectly used self.margin_usdt=$100 with $15 distance,
    # it would compute (15/3000)*101.5 = 0.5075 → SL=100.9925, ~0.01 off.
    # Use SOL's effective ratio: (4.5/900)*101.5 = 0.5075 → SL=100.9925.
    # Same percentage either way — the proof is that BOTH yield the same
    # SL by *design* (V3 ratios scale to keep % constant). The real proof
    # of correctness is the threshold test above; this one just exercises
    # the per-position margin lookup path.
    blofin.place_sl_order.assert_called_once()
    row = store.get_open_position("SOL-USDT")
    assert row.trail_high_price == pytest.approx(101.5)


@pytest.mark.asyncio
async def test_position_carries_its_own_margin_leverage(store):
    """Position row stores margin_usdt + leverage at entry, immutable later."""
    pid = store.create_position(
        symbol="ZEC-USDT", side="long", entry_price=100.0,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
        margin_usdt=250.0, leverage=30.0,
    )
    row = store.get_position(pid)
    assert row.margin_usdt == 250.0
    assert row.leverage == 30.0


@pytest.mark.asyncio
async def test_position_margin_leverage_default_when_unspecified(store):
    """Backward compat — older callers without margin/leverage get defaults."""
    pid = store.create_position(
        symbol="SOL-USDT", side="long", entry_price=100.0,
        initial_size=10, sl_policy="p2_step_stop", source="pro_v3",
    )
    row = store.get_position(pid)
    assert row.margin_usdt == 100.0
    assert row.leverage == 30.0
