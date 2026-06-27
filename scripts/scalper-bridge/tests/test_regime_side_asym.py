"""Per-side (long/short) entry-threshold test for regime_mr (2026-06-26).

The live long/short review + sim validation (side_asym_sweep.py / side_asym_validate.py)
found shorts are the edge and longs are a regime-dependent diversifier. The validated
improvement is to take MORE shorts WITHOUT touching longs: short z_entry 1.5->1.25 and
short min_slope_pct 0.08->0.05. That needs PER-SIDE thresholds in prepare_regime.

Contract proven here:
  (1) per-side params default to the symmetric base => byte-for-byte prior behavior;
  (2) a looser short z_entry arms MORE shorts, every added short has |z| in
      [z_entry_short, z_entry), and reg_long is UNCHANGED;
  (3) a looser short min_slope_pct arms MORE shorts, every added short has |slope%|
      in [min_slope_pct_short, min_slope_pct), and reg_long is UNCHANGED.

Run:
    docker exec scalper-bridge python -m pytest /app/tests/test_regime_side_asym.py -q
or standalone:
    python tests/test_regime_side_asym.py
"""
import numpy as np
import pandas as pd

from lighter_bridge.regime import prepare_regime


def _frame(n=1500, seed=7):
    """Random walk with alternating drift — arms many longs+shorts across a range
    of z-scores and EMA slopes, so per-side threshold changes have something to act on."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-06-01", periods=n, freq="15min", tz="UTC")
    drift = np.sin(np.arange(n) / 60.0) * 0.05
    steps = drift + rng.normal(0, 0.30, n)
    close = 100.0 + np.cumsum(steps)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close + 0.15,
                         "Low": close - 0.15, "Close": close,
                         "Volume": np.full(n, 1000.0)}, index=idx)


def test_per_side_defaults_are_identical():
    """Omitting per-side params (or setting them equal to the base) == prior behavior."""
    df = _frame()
    base = prepare_regime(df, z_entry=1.5, min_slope_pct=0.08)
    none_set = prepare_regime(df, z_entry=1.5, min_slope_pct=0.08,
                              z_entry_long=None, z_entry_short=None,
                              min_slope_pct_long=None, min_slope_pct_short=None)
    explicit = prepare_regime(df, z_entry=1.5, min_slope_pct=0.08,
                              z_entry_long=1.5, z_entry_short=1.5,
                              min_slope_pct_long=0.08, min_slope_pct_short=0.08)
    for other in (none_set, explicit):
        assert base["reg_long"].equals(other["reg_long"])
        assert base["reg_short"].equals(other["reg_short"])


def test_looser_short_z_entry_adds_shorts_only():
    """short z_entry 1.5->1.25: more shorts, longs untouched, added shorts in-band."""
    df = _frame()
    off = prepare_regime(df, z_entry=1.5, min_slope_pct=0.0)
    cand = prepare_regime(df, z_entry=1.5, min_slope_pct=0.0, z_entry_short=1.25)

    # longs are NOT affected by a short-side override
    assert off["reg_long"].equals(cand["reg_long"]), "long signals must be unchanged"

    s_off = off["reg_short"].values
    s_on = cand["reg_short"].values
    added = s_on & ~s_off
    assert (s_on >= s_off).all(), "looser threshold must never remove a short"
    assert added.sum() > 0, "fixture should add shorts in the 1.25-1.5 z band"
    z = cand["zscore"].values
    assert np.all(z[added] >= 1.25) and np.all(z[added] < 1.5), \
        "every added short must have |z| in [1.25, 1.5)"


def test_looser_short_slope_gate_adds_shorts_only():
    """short min_slope_pct 0.08->0.05: more shorts, longs untouched, added in-band."""
    df = _frame()
    off = prepare_regime(df, z_entry=1.5, min_slope_pct=0.08)
    cand = prepare_regime(df, z_entry=1.5, min_slope_pct=0.08, min_slope_pct_short=0.05)

    assert off["reg_long"].equals(cand["reg_long"]), "long signals must be unchanged"

    s_off = off["reg_short"].values
    s_on = cand["reg_short"].values
    added = s_on & ~s_off
    assert (s_on >= s_off).all(), "looser slope gate must never remove a short"
    assert added.sum() > 0, "fixture should add shorts in the 0.05-0.08 slope band"
    ev = cand["ema_trend"].values
    slv = cand["slope"].values
    slope_pct = np.abs(slv[added] / ev[added] * 100.0)
    assert np.all(slope_pct >= 0.05) and np.all(slope_pct < 0.08), \
        "every added short must have |slope%| in [0.05, 0.08)"


def test_regime_config_accepts_per_side_fields():
    """The loader maps yaml regime keys via RegimeConfig(**...); the new per-side
    fields must exist and default to None (=> base behavior)."""
    from lighter_bridge.config import RegimeConfig
    base = RegimeConfig()
    assert base.z_entry_short is None and base.min_slope_pct_short is None
    assert base.z_entry_long is None and base.min_slope_pct_long is None
    cfg = RegimeConfig(z_entry_short=1.25, min_slope_pct_short=0.05)
    assert cfg.z_entry_short == 1.25 and cfg.min_slope_pct_short == 0.05


if __name__ == "__main__":
    test_per_side_defaults_are_identical()
    test_looser_short_z_entry_adds_shorts_only()
    test_looser_short_slope_gate_adds_shorts_only()
    test_regime_config_accepts_per_side_fields()
    print("ok: per-side regime threshold tests passed")
