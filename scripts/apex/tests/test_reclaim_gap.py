"""Reclaim entry (M13) + max-entry-gap filter tests.

The Reclaim strategy = HA-V3 flip -> EMA9 retest that must CLOSE BACK across
EMA9 (reclaim) -> enter only when the close sits within max_gap_pct of EMA9.
Validated: scripts/scalping/v3.2-analysis/entry_v2_search.py (PF 1.27, OOS-stable,
1m-magnifier confirmed). These tests lock the two new primitives and the exact
decision ORDER that process_pending applies them in.

Run:
    docker exec reclaim-bridge python -m pytest /app/tests/test_reclaim_gap.py -q
or standalone (no lighter dep needed — pure signal helpers + yaml):
    python tests/test_reclaim_gap.py
"""
import numpy as np
import pytest

from apex_bridge.signals import (
    check_retest, check_reclaim, entry_gap_pct, passes_entry_filters,
)


# ---- check_reclaim: the bounce-confirmation half of M13 -------------------
def test_reclaim_long_requires_close_above_ema():
    # long: wick touched EMA9 (100) but must CLOSE BACK above it to be a bounce
    assert check_reclaim("long", 100.0, 100.5) is True     # closed above -> bounce
    assert check_reclaim("long", 100.0, 99.5) is False     # closed below -> breakdown
    assert check_reclaim("long", 100.0, 100.0) is False    # exactly on EMA = not reclaimed


def test_reclaim_short_requires_close_below_ema():
    assert check_reclaim("short", 100.0, 99.5) is True      # closed below -> bounce
    assert check_reclaim("short", 100.0, 100.5) is False    # closed above -> breakup
    assert check_reclaim("short", 100.0, 100.0) is False


def test_reclaim_nan_ema_is_false():
    assert check_reclaim("long", np.nan, 100.0) is False


# ---- entry_gap_pct: the cost-control half of M13 -------------------------
def test_entry_gap_pct_is_percent_distance_from_ema():
    assert entry_gap_pct(100.0, 100.05) == pytest.approx(0.05)   # 0.05% above
    assert entry_gap_pct(100.0, 99.95) == pytest.approx(0.05)    # symmetric below
    assert entry_gap_pct(100.0, 100.0) == 0.0


def test_entry_gap_pct_invalid_ema_forces_skip():
    # an inf gap is always > any cap -> the entry gets skipped (fail-safe)
    assert entry_gap_pct(np.nan, 100.0) == float("inf")
    assert entry_gap_pct(0.0, 100.0) == float("inf")


# ---- the composed decision ORDER (mirrors process_pending) ---------------
# touch (check_retest) -> reclaim (keep-pending if fail) -> filters (consume if
# fail) -> gap (consume if fail) -> FIRE. This standalone replica locks the
# contract the bridge wiring must honor.
def _decide(side, ema, low, high, close, slope, body, ts,
            max_gap=0.05, min_slope=0.15, body_band=(0.3, 0.5), weekdays=(6,)):
    if not check_retest(side, ema, low, high, 0.2):
        return "keep"                      # never touched EMA9
    if not check_reclaim(side, ema, close):
        return "keep"                      # touched but no bounce yet
    if abs(slope) < 0.03:
        return "keep"                      # base slope gate
    if not passes_entry_filters(ts, slope, body, list(weekdays), min_slope, body_band):
        return "skip_filter"               # consumed by F_LIVE
    if max_gap > 0 and entry_gap_pct(ema, close) > max_gap:
        return "skip_gap"                  # bounce ran too far from EMA9
    return "fire"


class _TS:
    """Minimal timestamp stub with weekday() (Wed = not blocked)."""
    def __init__(self, wd=2):
        self._wd = wd
    def weekday(self):
        return self._wd


def test_touch_without_reclaim_keeps_pending():
    # long wicks to EMA9 but closes below it -> breakdown, stay pending
    assert _decide("long", 100.0, 99.9, 100.2, 99.8, slope=0.3, body=1.0, ts=_TS()) == "keep"


def test_clean_reclaim_within_gap_fires():
    # wick to 99.9, close back at 100.03 (0.03% gap < 0.05 cap), good slope/body
    assert _decide("long", 100.0, 99.9, 100.1, 100.03, slope=0.3, body=1.0, ts=_TS()) == "fire"


def test_reclaim_but_gap_too_far_is_skipped():
    # bounce ran to 100.20 = 0.20% gap > 0.05 cap -> edge gone, skip
    assert _decide("long", 100.0, 99.9, 100.3, 100.20, slope=0.3, body=1.0, ts=_TS()) == "skip_gap"


def test_reclaim_blocked_by_flive_body_band():
    # body 0.4 sits in the blocked (0.3,0.5) chop band -> filtered out
    assert _decide("long", 100.0, 99.9, 100.1, 100.03, slope=0.3, body=0.4, ts=_TS()) == "skip_filter"


def test_reclaim_blocked_on_sunday():
    assert _decide("long", 100.0, 99.9, 100.1, 100.03, slope=0.3, body=1.0, ts=_TS(wd=6)) == "skip_filter"


def test_short_clean_reclaim_fires():
    assert _decide("short", 100.0, 99.9, 100.1, 99.97, slope=-0.3, body=1.0, ts=_TS()) == "fire"


# ---- config wiring -------------------------------------------------------
def test_config_loads_reclaim_fields():
    from pathlib import Path
    from apex_bridge.config import load_config
    cfg = load_config(Path(__file__).resolve().parents[1] / "config.reclaim.yaml")
    assert cfg.entry.require_reclaim is True
    assert cfg.entry.max_gap_pct == 0.05
    assert cfg.entry.min_abs_slope_pct == 0.15
    assert cfg.entry.block_body_band == (0.3, 0.5)
    assert cfg.signal_source == "replica"
    assert cfg.exit_model == "trail"
    assert cfg.exits.sl_loss_usdt == 82.5
    assert cfg.exits.tp_ceiling_pct == 2.0
    # all 7 coins, fixed $250/30x
    assert set(cfg.symbols) == {"BTC", "SOL", "DOGE", "XRP", "HYPE", "BNB", "ZEC"}
    for s in cfg.symbols.values():
        assert s.margin_usdt == 250 and s.leverage == 30 and s.enabled
    assert cfg.sizing.mode == "fixed"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"ok: {len(fns)} reclaim-gap tests passed")
