import pytest
from blofin_bridge.policies.base import Position, SLOrder
from blofin_bridge.policies.p2_step_stop import P2StepStop


@pytest.fixture
def long_position():
    return Position(
        symbol="SOL-USDT",
        side="long",
        entry_price=80.0,
        initial_size=12,
        current_size=12,
        tp_stage=0,
        tp1_fill_price=None,
        tp2_fill_price=None,
    )


@pytest.fixture
def short_position(long_position):
    return long_position._replace(side="short")


def test_p2_entry_long_places_safety_sl_below_entry(long_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_entry(long_position)
    assert sl.trigger_price == pytest.approx(76.0)   # 80 * 0.95
    assert sl.side == "sell"
    assert sl.size == -1                              # entire position


def test_p2_entry_short_places_safety_sl_above_entry(short_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_entry(short_position)
    assert sl.trigger_price == pytest.approx(84.0)   # 80 * 1.05
    assert sl.side == "buy"
    assert sl.size == -1


def test_p2_on_tp1_moves_long_sl_to_entry(long_position):
    pos_after_tp1 = long_position._replace(
        tp_stage=1, tp1_fill_price=82.0, current_size=7,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp1, tp_stage=1, tp_fill_price=82.0)
    assert sl.trigger_price == 80.0                   # entry (breakeven)
    assert sl.side == "sell"


def test_p2_on_tp2_moves_long_sl_to_tp1_price(long_position):
    pos_after_tp2 = long_position._replace(
        tp_stage=2, tp1_fill_price=82.0, tp2_fill_price=84.0, current_size=4,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp2, tp_stage=2, tp_fill_price=84.0)
    assert sl.trigger_price == 82.0                   # TP1 fill price
    assert sl.side == "sell"


def test_p2_on_tp3_returns_none_no_sl_needed(long_position):
    pos_after_tp3 = long_position._replace(
        tp_stage=3, tp1_fill_price=82.0, tp2_fill_price=84.0, current_size=0,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    # On TP3, position is fully closed — no new SL to set
    assert policy.on_tp(pos_after_tp3, tp_stage=3, tp_fill_price=86.0) is None


def test_p2_short_on_tp1_moves_sl_to_entry_from_above(short_position):
    pos_after_tp1 = short_position._replace(
        tp_stage=1, tp1_fill_price=78.0, current_size=7,
    )
    policy = P2StepStop(safety_sl_pct=0.05)
    sl = policy.on_tp(pos_after_tp1, tp_stage=1, tp_fill_price=78.0)
    assert sl.trigger_price == 80.0                   # entry
    assert sl.side == "buy"                           # short SL is a buy


def test_p2_on_tick_is_noop(long_position):
    policy = P2StepStop(safety_sl_pct=0.05)
    assert policy.on_tick(long_position, last_price=100.0) is None
