"""Filter contracts shared by all filters in the runner pipeline."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from runner.enrichment.schemas import EnrichedToken


@dataclass(frozen=True, eq=False)
class FilterResult:
    """Result of running one filter against one EnrichedToken.

    `passed` is False only for hard-gate failures (e.g. LP not locked,
    mint authority still enabled). Soft scoring filters (Entry Quality,
    Follow-through) always return True and populate `sub_scores`.
    """

    filter_name: str
    passed: bool
    hard_fail_reason: str | None
    sub_scores: dict[str, float] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, eq=False)
class FilteredCandidate:
    """A candidate that has been through the full filter pipeline.

    `gate_passed=False` means at least one hard gate failed and the
    pipeline short-circuited; only the failing filter's result is in
    `filter_results`. `gate_passed=True` means every filter ran and
    every hard gate passed.
    """

    enriched: EnrichedToken
    filter_results: list[FilterResult]
    gate_passed: bool
    hard_fail_reason: str | None
    hard_fail_filter_name: str | None = None


class BaseFilter(ABC):
    """Abstract base for all filters in the runner pipeline.

    Each concrete filter sets `name` as a class attribute and implements
    `apply(enriched)` to return a FilterResult. Filters should never raise
    on expected failures (API errors, missing data) — instead return a
    FilterResult with `passed=False` or with `sub_scores` reflecting
    the degraded confidence.
    """

    name: str = "base"

    @abstractmethod
    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        """Run this filter against a candidate and return a FilterResult."""
        raise NotImplementedError
