"""MilestoneSnapshotter exit policy — hard SL, trail stop, time stop."""
import pytest

from runner.executor.snapshotter import MilestoneSnapshotter


def _snapshotter(**kw) -> MilestoneSnapshotter:
    defaults = dict(
        alert_bus=None, price_fetcher=None, db=None,
        stop_loss_pct=25.0,
        trail_activate_pct=30.0,
        trail_distance_pct=20.0,
        trail_breakeven_floor_pct=5.0,
        time_stop_sec=14400.0,
        time_stop_pnl_max=0.0,
    )
    defaults.update(kw)
    return MilestoneSnapshotter(**defaults)


def test_no_exit_when_position_neutral():
    s = _snapshotter()
    assert s._decide_exit(peak_pnl_pct=5.0, current_pnl_pct=2.0, elapsed_sec=600) is None


def test_hard_stop_loss_fires():
    s = _snapshotter()
    assert s._decide_exit(peak_pnl_pct=5.0, current_pnl_pct=-25.0, elapsed_sec=300) == "stopped_out"
    assert s._decide_exit(peak_pnl_pct=5.0, current_pnl_pct=-26.0, elapsed_sec=300) == "stopped_out"
    # Just above SL threshold = no exit
    assert s._decide_exit(peak_pnl_pct=5.0, current_pnl_pct=-24.9, elapsed_sec=300) is None


def test_trail_stop_does_not_arm_below_threshold():
    s = _snapshotter()
    # Peak is 25% (below 30% activation); a -10% give-back doesn't trigger
    assert s._decide_exit(peak_pnl_pct=25.0, current_pnl_pct=15.0, elapsed_sec=600) is None


def test_trail_stop_fires_after_arming():
    s = _snapshotter()
    # Peak hit 50%; pnl now 25% = give-back of 25% > trail_distance_pct of 20%
    assert s._decide_exit(peak_pnl_pct=50.0, current_pnl_pct=25.0, elapsed_sec=600) == "trail_stop"


def test_trail_locks_in_profit_when_giving_back_exactly_distance():
    s = _snapshotter()
    # Peak 40, current 20, give-back 20 = exactly trail_distance → fires
    assert s._decide_exit(peak_pnl_pct=40.0, current_pnl_pct=20.0, elapsed_sec=600) == "trail_stop"


def test_time_stop_fires_when_underwater_and_old():
    s = _snapshotter()
    # 5h elapsed, pnl is -5% (under 0 threshold) → time_stop
    assert s._decide_exit(peak_pnl_pct=10.0, current_pnl_pct=-5.0, elapsed_sec=18000) == "time_stop"


def test_time_stop_does_not_fire_if_position_profitable():
    s = _snapshotter()
    # 5h elapsed but still up 10% → no time_stop, give it more rope
    assert s._decide_exit(peak_pnl_pct=10.0, current_pnl_pct=10.0, elapsed_sec=18000) is None


def test_stop_loss_takes_precedence_over_trail():
    s = _snapshotter()
    # Peak 60% but current is -30% — SL fires first (we never hit trail give-back path)
    assert s._decide_exit(peak_pnl_pct=60.0, current_pnl_pct=-30.0, elapsed_sec=600) == "stopped_out"


def test_breakeven_floor_fires_after_arming():
    """Once the trail has armed (peak >= trail_activate_pct), if pnl falls
    to/below the breakeven floor, exit — don't wait for full giveback."""
    s = _snapshotter()
    # Peak 32% (trail armed); current +4% — below breakeven floor (5%)
    assert s._decide_exit(peak_pnl_pct=32.0, current_pnl_pct=4.0, elapsed_sec=600) == "trail_breakeven_floor"
    # Exactly at floor fires
    assert s._decide_exit(peak_pnl_pct=32.0, current_pnl_pct=5.0, elapsed_sec=600) == "trail_breakeven_floor"
    # Below zero also fires via breakeven floor (before hitting hard SL)
    assert s._decide_exit(peak_pnl_pct=32.0, current_pnl_pct=-10.0, elapsed_sec=600) == "trail_breakeven_floor"


def test_breakeven_floor_does_not_fire_before_arming():
    """If trail has NOT armed (peak < trail_activate_pct), breakeven floor
    does not apply — only the hard SL protects the position."""
    s = _snapshotter()
    # Peak only 28% — trail never armed, floor inactive
    assert s._decide_exit(peak_pnl_pct=28.0, current_pnl_pct=4.0, elapsed_sec=600) is None
    assert s._decide_exit(peak_pnl_pct=28.0, current_pnl_pct=-10.0, elapsed_sec=600) is None


def test_plan_example_sequence_breakeven_closes_at_10():
    """Plan verification case: pnl sequence [0, +32%, +40%, +10%] should
    close at +10% with close_reason='trail_stop' OR 'trail_breakeven_floor'.
    At 40→10 the giveback is 30% which triggers trail_stop, not floor —
    floor would fire earlier at +5% or below. Confirm trail_stop path."""
    s = _snapshotter()
    # Peak tracked as max_favorable=40; current=10; giveback=30
    assert s._decide_exit(peak_pnl_pct=40.0, current_pnl_pct=10.0, elapsed_sec=600) == "trail_stop"
    # But if price bled to +3% instead, floor fires first
    assert s._decide_exit(peak_pnl_pct=40.0, current_pnl_pct=3.0, elapsed_sec=600) == "trail_breakeven_floor"
