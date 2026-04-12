"""Scoring data model — ScoredCandidate and supporting types."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from runner.filters.base import FilteredCandidate

Verdict = Literal["ignore", "watch", "strong_candidate", "probable_runner"]

DIMENSION_KEYS: tuple[str, ...] = (
    "wallet_quality",
    "cluster_quality",
    "entry_quality",
    "holder_quality",
    "rug_risk",
    "follow_through",
    "narrative",
)


@dataclass(frozen=True, eq=False)
class ScoredCandidate:
    """A candidate that has been scored by the ScoringEngine.

    `dimension_scores` always has all 7 DIMENSION_KEYS present (zeroed
    for short-circuited candidates). Keys match weights.yaml weight keys.
    """

    filtered: FilteredCandidate
    runner_score: float
    verdict: Verdict
    dimension_scores: dict[str, float]
    explanation: dict[str, Any]
    scored_at: datetime
