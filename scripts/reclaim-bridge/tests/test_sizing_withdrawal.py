"""Unit tests for the pure sizing + withdrawal policy (behaves identically for
paper and live — these assert the math the real account will rely on)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from lighter_bridge.sizing import compound_margin, withdrawal_surplus  # noqa: E402

BASE_M, BASE_EQ, CAP = 500.0, 3600.0, 3.0


# ---- compounding sizing ----
def test_at_base_equity_is_base_margin():
    assert compound_margin(BASE_M, BASE_EQ, BASE_EQ, CAP) == 500.0


def test_scales_up_linearly_below_cap():
    assert compound_margin(BASE_M, 7200.0, BASE_EQ, CAP) == 1000.0   # 2x equity -> 2x margin


def test_caps_at_cap_mult():
    assert compound_margin(BASE_M, 14400.0, BASE_EQ, CAP) == 1500.0  # would be 4x, capped 3x
    assert compound_margin(BASE_M, 1e9, BASE_EQ, CAP) == 1500.0


def test_exactly_at_target_hits_cap():
    assert compound_margin(BASE_M, BASE_EQ * CAP, BASE_EQ, CAP) == 1500.0  # target 10,800 -> cap


def test_scales_down_on_drawdown():
    assert compound_margin(BASE_M, 1800.0, BASE_EQ, CAP) == 250.0    # half equity -> half margin


def test_never_negative_on_blown_account():
    assert compound_margin(BASE_M, -500.0, BASE_EQ, CAP) == 0.0
    assert compound_margin(BASE_M, 0.0, BASE_EQ, CAP) == 0.0


def test_degenerate_base_equity_falls_back_to_base():
    assert compound_margin(BASE_M, 5000.0, 0.0, CAP) == 500.0


# ---- withdrawal ----
def test_no_withdraw_at_or_below_target():
    assert withdrawal_surplus(10000.0, BASE_EQ, CAP) == 0.0
    assert withdrawal_surplus(10800.0, BASE_EQ, CAP) == 0.0


def test_withdraw_surplus_above_target():
    assert withdrawal_surplus(11000.0, BASE_EQ, CAP) == 200.0


def test_withdraw_scales_with_target_mult():
    assert withdrawal_surplus(7300.0, BASE_EQ, 2.0) == 100.0   # target 7,200


def test_withdraw_disabled_when_target_mult_zero():
    assert withdrawal_surplus(99999.0, BASE_EQ, 0.0) == 0.0


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(["python", "-m", "pytest", __file__, "-q"]))
