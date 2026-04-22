"""Pure scoring functions.

All component scores are 0-100 ints. The composite is 0-100 float; main.py
divides by 10 for the user-facing 0-10 display score.

Source of truth for formulas: docs/scoring-rules.md (v0).
"""
from __future__ import annotations

import math
from typing import Optional


def flatness_score(highs_14d: list, lows_14d: list, closes_21d: list) -> int:
    """Tight range + low realized vol → high score."""
    if len(closes_21d) < 14 or len(highs_14d) < 14 or len(lows_14d) < 14:
        return 0

    closes = [float(x) for x in closes_21d]
    mean_close_14 = sum(closes[-14:]) / 14.0
    if mean_close_14 <= 0:
        return 0

    high_max = max(float(h) for h in highs_14d[-14:])
    low_min = min(float(l) for l in lows_14d[-14:])
    range_pct = (high_max - low_min) / mean_close_14

    log_returns = []
    for i in range(1, len(closes)):
        prev, curr = closes[i - 1], closes[i]
        if prev > 0 and curr > 0:
            log_returns.append(math.log(curr / prev))
    if len(log_returns) < 10:
        return 0
    n = len(log_returns)
    mean_lr = sum(log_returns) / n
    var = sum((r - mean_lr) ** 2 for r in log_returns) / (n - 1)
    rv = math.sqrt(var) * math.sqrt(365)

    range_score = _band(range_pct, [
        (0.05, 100), (0.08, 80), (0.12, 60),
        (0.18, 40), (0.25, 20),
    ], default=0)
    rv_score = _band(rv, [
        (0.40, 100), (0.60, 80), (0.85, 60),
        (1.10, 40), (1.50, 20),
    ], default=0)
    return int(round(0.5 * range_score + 0.5 * rv_score))


def funding_score(
    funding_now: float,
    funding_avg_14d: float,
    recent_flip_negative: bool = False,
) -> int:
    """Negative funding → high score. Strong negative + recent flip = max."""
    score = _band(funding_avg_14d, [
        (-0.0005, 100),
        (0.0,      80),
        (0.0001,   60),
        (0.0003,   40),
        (0.0005,   20),
    ], default=0)
    if recent_flip_negative and funding_now < 0:
        score = min(100, score + 10)
    return int(score)


def oi_growth_score(
    oi_now: Optional[float],
    oi_7d_ago: Optional[float],
    oi_14d_ago: Optional[float],
) -> Optional[int]:
    """Returns None when history is insufficient (new listing, missing data)."""
    if not oi_now or not oi_7d_ago or not oi_14d_ago:
        return None
    if oi_7d_ago <= 0 or oi_14d_ago <= 0:
        return None
    g7 = (oi_now - oi_7d_ago) / oi_7d_ago
    g14 = (oi_now - oi_14d_ago) / oi_14d_ago
    blend = 0.6 * g7 + 0.4 * g14
    if blend < 0:    return 0
    if blend < 0.05: return 20
    if blend < 0.10: return 40
    if blend < 0.20: return 60
    if blend < 0.30: return 80
    return 100


def non_pumped_score(return_7d: float, return_30d: float) -> int:
    """Penalize coins that already moved. Uses max of 7d/30d returns."""
    max_ret = max(return_7d, return_30d)
    return int(_band(max_ret, [
        (0.05, 100),
        (0.15,  80),
        (0.30,  50),
        (0.60,  20),
    ], default=0))


def liquidity_score(quote_volume_24h: float) -> int:
    """Hard gate: <$1M is untradable; ramps to 100 at $5M+."""
    if quote_volume_24h < 1_000_000:
        return 0
    if quote_volume_24h < 5_000_000:
        return 50
    return 100


def composite(scores: dict, weights: dict) -> float:
    """Combine component scores; re-normalize weights when a component is None.

    If liquidity_score is 0, caller should bypass and set composite to 0.
    """
    active = {k: w for k, w in weights.items() if scores.get(k) is not None}
    if not active:
        return 0.0
    total = sum(active.values())
    if total <= 0:
        return 0.0
    return sum(scores[k] * (w / total) for k, w in active.items())


def _band(value: float, bands: list, default: float) -> float:
    """bands is [(upper_bound, score), ...] in ascending order. First match wins."""
    for upper, score in bands:
        if value <= upper:
            return score
    return default
