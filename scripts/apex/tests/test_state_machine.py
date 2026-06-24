"""Apex 3-stage exit ladder: SL -$30 -> BE at +$20 -> at +$35 lock +$20 & trail $15."""
from dataclasses import dataclass

import pytest

from apex_bridge.state_machine import step, initial_sl
from apex_bridge.config import ExitConfig


@dataclass
class FakePos:
    side: str
    entry_price: float
    base_amount: float
    notional: float
    margin_usdt: float = 250.0
    symbol: str = "SOL"
    sl_price: float = 0.0
    state: int = 0
    trail_high: float = 0.0
    max_state: int = 0


def cfg():
    # $250 margin x 30x = $7,500 notional
    return ExitConfig(
        sl_loss_usdt=30.0, breakeven_usdt=20.0,
        trail_activate_usdt=35.0, trail_distance_usdt=15.0,
        tp_ceiling_pct=2.0,
    )


def _pos(side="long", entry=100.0):
    # notional 7500 at entry 100 -> base_amount 75; $1 PnL = 0.01333 price move
    return FakePos(side=side, entry_price=entry, base_amount=75.0, notional=7500.0,
                   trail_high=entry)


def _price_for_pnl(pos, usd):
    move = (usd / pos.notional) * pos.entry_price
    return pos.entry_price + move if pos.side == "long" else pos.entry_price - move


def test_initial_sl_is_minus_30():
    pos = _pos()
    px = initial_sl(pos, cfg())
    # -$30 on $7,500 notional at entry 100 = -0.4% = 99.6
    assert px == pytest.approx(99.6, abs=1e-6)


def test_breakeven_at_plus_20():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    mark = _price_for_pnl(pos, 20.0)
    step(pos, mark, c)
    assert pos.state == 1
    assert pos.sl_price == pytest.approx(pos.entry_price, abs=1e-6)


def test_below_35_stays_breakeven():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 25.0), c)
    assert pos.state == 1
    assert pos.sl_price == pytest.approx(pos.entry_price, abs=1e-6)


def test_at_35_locks_plus_20_and_trails():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)
    assert pos.state == 2
    # SL now locks +$20 (= +$35 peak minus $15 trail)
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 20.0), abs=1e-4)


def test_trailing_ratchets_15_behind_new_high():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)      # enter trailing
    step(pos, _price_for_pnl(pos, 60.0), c)      # new high +$60
    # SL trails $15 behind the +$60 peak -> +$45
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 45.0), abs=1e-4)


def test_trailing_does_not_lower_sl_on_pullback():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 60.0), c)      # jump straight to trailing, SL ~ +$45
    locked = pos.sl_price
    step(pos, _price_for_pnl(pos, 50.0), c)      # pull back to +$50 (no new high)
    assert pos.sl_price == pytest.approx(locked, abs=1e-9)


def test_sl_hit_reason_by_state():
    pos, c = _pos(), cfg()
    pos.sl_price = initial_sl(pos, c)
    d = step(pos, _price_for_pnl(pos, -30.0), c)   # straight to initial SL
    assert d.close and d.reason == "sl"


def test_short_symmetry_at_35():
    pos, c = _pos(side="short"), cfg()
    pos.sl_price = initial_sl(pos, c)
    step(pos, _price_for_pnl(pos, 35.0), c)
    assert pos.state == 2
    assert pos.sl_price == pytest.approx(_price_for_pnl(pos, 20.0), abs=1e-4)
