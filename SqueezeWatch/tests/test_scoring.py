"""Unit tests for src/scoring.py — see docs/scoring-rules.md for expected values."""

import math

from src import scoring


# ---------- flatness_score ----------

def test_flatness_tight_range_low_vol_scores_high():
    # 21 closes within ~1% of each other => very low rv, very tight range
    closes = [100.0, 100.1, 99.9, 100.2, 100.0, 99.8, 100.1, 100.0, 99.9,
              100.0, 100.1, 100.0, 99.95, 100.05, 100.0, 99.92, 100.08,
              100.0, 100.0, 99.99, 100.01]
    highs = [c + 0.2 for c in closes[-14:]]
    lows = [c - 0.2 for c in closes[-14:]]
    s = scoring.flatness_score(highs, lows, closes)
    assert s >= 90, f"expected >=90, got {s}"


def test_flatness_wide_range_high_vol_scores_low():
    # Volatile series spanning a 30% range
    closes = [100.0]
    for i in range(1, 21):
        closes.append(closes[-1] * (1.05 if i % 2 == 0 else 0.96))
    highs = [c * 1.05 for c in closes[-14:]]
    lows = [c * 0.92 for c in closes[-14:]]
    s = scoring.flatness_score(highs, lows, closes)
    assert s <= 30, f"expected <=30, got {s}"


def test_flatness_returns_zero_on_short_input():
    assert scoring.flatness_score([], [], []) == 0
    assert scoring.flatness_score([1] * 5, [1] * 5, [1] * 5) == 0


# ---------- funding_score ----------

def test_funding_strong_negative_scores_100():
    assert scoring.funding_score(-0.001, -0.001) == 100


def test_funding_zero_scores_80():
    assert scoring.funding_score(0.0, 0.0) == 80


def test_funding_high_positive_scores_zero():
    assert scoring.funding_score(0.001, 0.001) == 0


def test_funding_recent_flip_adds_bonus():
    base = scoring.funding_score(-0.0002, 0.0001)
    bonus = scoring.funding_score(-0.0002, 0.0001, recent_flip_negative=True)
    assert bonus == base + 10


def test_funding_no_bonus_when_now_positive():
    base = scoring.funding_score(0.0001, 0.0001)
    bonus = scoring.funding_score(0.0001, 0.0001, recent_flip_negative=True)
    assert bonus == base  # current positive => no flip bonus


# ---------- oi_growth_score ----------

def test_oi_growth_returns_none_on_missing_history():
    assert scoring.oi_growth_score(None, 100, 100) is None
    assert scoring.oi_growth_score(100, None, 100) is None
    assert scoring.oi_growth_score(100, 100, None) is None
    assert scoring.oi_growth_score(100, 0, 100) is None


def test_oi_growth_negative_scores_zero():
    # OI shrank
    assert scoring.oi_growth_score(80, 100, 100) == 0


def test_oi_growth_30pct_scores_100():
    # Both 7d and 14d up 30%+
    assert scoring.oi_growth_score(130, 100, 100) == 100


def test_oi_growth_3pct_scores_20():
    # 3% blend → in [0, 5%) band → 20
    assert scoring.oi_growth_score(103, 100, 100) == 20


def test_oi_growth_7pct_scores_40():
    # 7% blend → in [5%, 10%) band → 40
    assert scoring.oi_growth_score(107, 100, 100) == 40


def test_oi_growth_15pct_scores_60():
    # 15% blend → in [10%, 20%) band → 60
    assert scoring.oi_growth_score(115, 100, 100) == 60


# ---------- non_pumped_score ----------

def test_non_pumped_flat_scores_100():
    assert scoring.non_pumped_score(0.0, 0.0) == 100


def test_non_pumped_already_ran_scores_zero():
    assert scoring.non_pumped_score(0.5, 0.8) == 0


def test_non_pumped_uses_max_of_absolute_returns():
    # 7d weak but 30d big (up) => penalize on 30d
    assert scoring.non_pumped_score(0.0, 0.40) == 20


def test_non_pumped_penalizes_big_drawdown():
    # The SAGAUSDT case from the 2026-04-22 full-universe run: -40% 30d.
    # Pre-fix this returned 100 ("pre-move"); post-fix it must reject it.
    assert scoring.non_pumped_score(-0.32, -0.40) == 20


def test_non_pumped_symmetric_for_pump_and_crash():
    # A +40% pump and a -40% crash violate the "flat price" thesis equally.
    up = scoring.non_pumped_score(0.0, 0.40)
    down = scoring.non_pumped_score(0.0, -0.40)
    assert up == down == 20


def test_non_pumped_mixed_direction_uses_largest_magnitude():
    # Small positive 7d (+3%) with a big negative 30d (-25%) => band uses |30d|
    assert scoring.non_pumped_score(0.03, -0.25) == 50


def test_non_pumped_tiny_drift_still_scores_100():
    # Staying within +/- 5% either way is still "flat enough"
    assert scoring.non_pumped_score(-0.04, 0.03) == 100


# ---------- liquidity_score ----------

def test_liquidity_below_floor_zero():
    assert scoring.liquidity_score(500_000) == 0


def test_liquidity_mid_range_50():
    assert scoring.liquidity_score(2_500_000) == 50


def test_liquidity_above_full_100():
    assert scoring.liquidity_score(20_000_000) == 100


# ---------- composite ----------

WEIGHTS = {
    "flatness":   0.30,
    "funding":    0.20,
    "oi_growth":  0.25,
    "non_pumped": 0.15,
    "liquidity":  0.10,
}


def test_composite_all_components_present():
    scores = {
        "flatness": 100, "funding": 100, "oi_growth": 100,
        "non_pumped": 100, "liquidity": 100,
    }
    assert scoring.composite(scores, WEIGHTS) == 100.0


def test_composite_renormalizes_when_oi_is_none():
    # Drop oi_growth (weight 0.25) => remaining weights sum 0.75 and re-normalize
    scores = {
        "flatness": 100, "funding": 100, "oi_growth": None,
        "non_pumped": 100, "liquidity": 100,
    }
    # All remaining components score 100, so composite must still be 100
    assert math.isclose(scoring.composite(scores, WEIGHTS), 100.0)


def test_composite_zero_when_all_components_none():
    scores = {k: None for k in WEIGHTS}
    assert scoring.composite(scores, WEIGHTS) == 0.0


def test_composite_partial_scores():
    # Hand-computed: 60*0.30 + 80*0.20 + 40*0.25 + 100*0.15 + 100*0.10
    # = 18 + 16 + 10 + 15 + 10 = 69.0
    scores = {
        "flatness": 60, "funding": 80, "oi_growth": 40,
        "non_pumped": 100, "liquidity": 100,
    }
    assert math.isclose(scoring.composite(scores, WEIGHTS), 69.0)
