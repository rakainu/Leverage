import math

from lighter_dashboard import stats


def test_win_rate_basic():
    assert stats.win_rate([10.0, -5.0, 3.0, -2.0]) == 0.5


def test_win_rate_empty():
    assert stats.win_rate([]) == 0.0


def test_profit_factor():
    # gross win 13, gross loss 7
    assert math.isclose(stats.profit_factor([10.0, -5.0, 3.0, -2.0]), 13 / 7)


def test_profit_factor_no_losses_returns_none():
    assert stats.profit_factor([10.0, 3.0]) is None


def test_max_drawdown():
    # equity peaks at 120 then dips to 90 -> drawdown -30
    series = [100.0, 120.0, 90.0, 110.0]
    assert stats.max_drawdown(series) == -30.0


def test_max_drawdown_monotonic_up():
    assert stats.max_drawdown([100.0, 110.0, 130.0]) == 0.0


def test_unrealized_pnl_long():
    assert stats.unrealized_pnl("long", entry=100.0, mark=105.0, base=2.0) == 10.0


def test_unrealized_pnl_short():
    assert stats.unrealized_pnl("short", entry=100.0, mark=95.0, base=2.0) == 10.0
