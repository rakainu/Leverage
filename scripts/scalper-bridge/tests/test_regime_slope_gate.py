"""Trend-clarity gate test for regime_mr (2026-06-20).

The sign-only trend gate shorts a flat-but-rising tape and gets run over.
`min_slope_pct>0` requires |EMA-slope|% >= threshold before fading against the
trend. Proves: (1) min_slope_pct=0 is byte-for-byte the prior behavior; (2) a
signal arming on a near-flat EMA slope is suppressed when the gate is on; (3) a
signal on a clearly-sloped trend still fires with the gate on.

Run:
    docker exec scalper-bridge python -m pytest /app/tests/test_regime_slope_gate.py -q
or standalone:
    python tests/test_regime_slope_gate.py
"""
import numpy as np
import pandas as pd

from lighter_bridge.regime import prepare_regime


def _frame(n=800, seed=11):
    """A random walk with drift swings — produces many regime signals across a
    range of EMA slopes (flat and steep), so the gate has something to filter."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="15min", tz="UTC")
    drift = np.sin(np.arange(n) / 60.0) * 0.05            # alternating trend
    steps = drift + rng.normal(0, 0.30, n)
    close = 100.0 + np.cumsum(steps)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close + 0.15,
                         "Low": close - 0.15, "Close": close,
                         "Volume": np.full(n, 1000.0)}, index=idx)


def _armed(df):
    return df["reg_long"].values | df["reg_short"].values


def test_gate_off_is_identical():
    df = _frame()
    a = prepare_regime(df, min_slope_pct=0.0)
    b = prepare_regime(df)
    assert a["reg_long"].equals(b["reg_long"])
    assert a["reg_short"].equals(b["reg_short"])


def test_gate_only_removes_flat_slope_signals():
    df = _frame()
    off = prepare_regime(df, min_slope_pct=0.0)
    on = prepare_regime(df, min_slope_pct=0.08)
    a_off, a_on = _armed(off), _armed(on)
    # the fixture must generate signals to make this meaningful
    assert a_off.sum() > 10, "fixture should arm plenty of signals"
    # gate NEVER adds a signal (every on-signal was already an off-signal)
    assert np.all(a_on <= a_off), "gate must not add signals"
    # gate actually removes some (there are flat-slope signals to drop)
    assert a_on.sum() < a_off.sum(), "gate should remove some flat-slope signals"
    # every REMOVED signal had |slope%| below the threshold — the exact contract
    ev = off["ema_trend"].values
    slv = off["slope"].values
    removed = a_off & ~a_on
    slope_pct = np.abs(slv[removed] / ev[removed] * 100.0)
    assert np.all(slope_pct < 0.08), "only sub-threshold-slope signals are removed"


def test_steep_slope_signals_survive():
    df = _frame()
    off = prepare_regime(df, min_slope_pct=0.0)
    on = prepare_regime(df, min_slope_pct=0.08)
    a_off, a_on = _armed(off), _armed(on)
    ev = off["ema_trend"].values
    slv = off["slope"].values
    # any off-signal whose |slope%| clears the threshold must STILL be armed
    kept_expected = a_off & (np.abs(slv / ev * 100.0) >= 0.08)
    assert np.all(a_on[kept_expected]), "clear-trend signals must survive the gate"


if __name__ == "__main__":
    test_gate_off_is_identical()
    test_gate_only_removes_flat_slope_signals()
    test_steep_slope_signals_survive()
    print("ok: trend-clarity gate tests passed")
