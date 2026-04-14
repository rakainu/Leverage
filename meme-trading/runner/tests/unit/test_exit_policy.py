"""MilestoneSnapshotter exit policy — hard SL, trail stop, time stop."""
import pytest

from runner.executor.snapshotter import MilestoneSnapshotter


def _snapshotter(**kw) -> MilestoneSnapshotter:
    defaults = dict(
        alert_bus=None, price_fetcher=None, db=None,
        stop_loss_pct=25.0,
        trail_activate_pct=30.0,
        trail_distance_pct=20.0,
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
