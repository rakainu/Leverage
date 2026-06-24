"""Accel-guard test for regime_mr (2026-06-20).

The scalper fades z-extensions; the rare full-sl_atr loss comes from fading a
news-rip / volatility-climax bar that keeps going. `accel_mult>0` declines to
fire on a signal bar whose range (High-Low) >= accel_mult*ATR.

Proves: (1) accel_mult=0 is byte-for-byte the prior behavior; (2) a climax bar
that WOULD fire a regime signal is suppressed when the guard is on; (3) a normal
(non-climax) signal bar still fires with the guard on.

Run:
    docker exec scalper-bridge python -m pytest /app/tests/test_regime_accel_guard.py -q
or standalone:
    python tests/test_regime_accel_guard.py
"""
import numpy as np
import pandas as pd

from apex_bridge.regime import prepare_regime


def _series(n=400, seed=7):
    """A downtrend with a terminal up-rip so a regime SHORT signal is armed on
    the last bar; we then inflate that bar's range to make it a climax."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="15min", tz="UTC")
    # steady downtrend (sets slope<0) then a sharp pop up (z>=+entry on last bar)
    base = np.linspace(100.0, 80.0, n) + rng.normal(0, 0.05, n)
    base[-1] = base[-2] + 3.0  # rip up on the final bar -> +z extension
    close = base
    high = close + 0.10
    low = close - 0.10
    openp = np.r_[close[0], close[:-1]]
    vol = np.full(n, 1000.0)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def test_guard_off_is_identical():
    df = _series()
    a = prepare_regime(df, accel_mult=0.0)
    b = prepare_regime(df)  # default
    assert a["reg_long"].equals(b["reg_long"])
    assert a["reg_short"].equals(b["reg_short"])


def test_climax_bar_suppressed_by_guard():
    df = _series()
    off = prepare_regime(df, accel_mult=0.0)
    fired = off["reg_long"].iloc[-1] or off["reg_short"].iloc[-1]
    assert fired, "fixture must arm a signal on the last bar with the guard OFF"

    # Inflate the signal bar's range to a clear climax (>3x its ATR) and confirm
    # the guard suppresses it while the guard-off result still fires.
    d2 = df.copy()
    atr_last = prepare_regime(df, accel_mult=0.0)["atr14"].iloc[-1]
    mid = d2["Close"].iloc[-1]
    d2.iloc[-1, d2.columns.get_loc("High")] = mid + 2.0 * atr_last
    d2.iloc[-1, d2.columns.get_loc("Low")] = mid - 2.0 * atr_last  # range = 4*ATR
    on = prepare_regime(d2, accel_mult=3.0)
    off2 = prepare_regime(d2, accel_mult=0.0)
    assert (off2["reg_long"].iloc[-1] or off2["reg_short"].iloc[-1]), \
        "guard OFF should still fire on the climax bar"
    assert not (on["reg_long"].iloc[-1] or on["reg_short"].iloc[-1]), \
        "guard ON (3.0) should suppress the 4*ATR climax signal bar"


def test_normal_signal_still_fires_with_guard():
    df = _series()  # last-bar range is 0.20, ATR ~0.x -> not a climax at 3.0
    on = prepare_regime(df, accel_mult=3.0)
    off = prepare_regime(df, accel_mult=0.0)
    # the small-range rip is below 3*ATR, so the guard must NOT suppress it
    assert (off["reg_long"].iloc[-1] or off["reg_short"].iloc[-1])
    assert (on["reg_long"].iloc[-1] or on["reg_short"].iloc[-1]), \
        "a normal (non-climax) signal bar must still fire with the guard on"


if __name__ == "__main__":
    test_guard_off_is_identical()
    test_climax_bar_suppressed_by_guard()
    test_normal_signal_still_fires_with_guard()
    print("ok: accel-guard tests passed")
