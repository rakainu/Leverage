# Runner Plan 2b — Filter Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the filter pipeline that consumes `EnrichedToken` objects and produces a list of `FilterResult`s per candidate — including the async 5-minute follow-through probe — with DB persistence and pipeline orchestration that short-circuits on hard-gate failures.

**Architecture:** Five independent filters conforming to a `BaseFilter` contract (each takes an `EnrichedToken`, returns a `FilterResult` with pass/fail + sub-scores + evidence). Fast synchronous filters run in sequence with hard-gate short-circuiting; the slow `FollowThroughProbe` runs last only on candidates that pass all sync gates. The `FilterPipeline` orchestrator spawns a task per `EnrichedToken` so 5-minute probes don't block other candidates. Results persist to new `filter_results` and `runner_scores` tables (the latter is written to by Plan 2c).

**Tech Stack:**
- Python 3.11+, asyncio
- `aiosqlite` (persistence)
- `httpx` via `RateLimitedClient` for RugCheck (`api.rugcheck.xyz`) + Helius DAS + DexScreener
- `pytest`, `pytest-asyncio`, `respx`, `freezegun` (time control for probe tests)

**Reference spec:** `docs/superpowers/specs/2026-04-11-meme-runner-design.md`
**Preceding plans:**
- `docs/superpowers/plans/2026-04-11-runner-foundation-ingest-cluster.md` (Plan 1, complete)
- `docs/superpowers/plans/2026-04-11-runner-followups-and-enrichment.md` (Plan 2a, complete)

**Parent folder:** All file paths below are relative to `meme-trading/runner/` unless stated otherwise.

---

## File Structure

**New package:**

```
meme-trading/runner/
├── filters/                        # NEW package
│   ├── __init__.py
│   ├── base.py                     # FilterResult, FilteredCandidate, BaseFilter
│   ├── rug_gate.py                 # Task 3 — RugCheck /report/summary
│   ├── holder_filter.py            # Task 4 — Helius DAS getTokenAccounts
│   ├── insider_filter.py           # Task 5 — RugCheck /insiders/graph
│   ├── entry_quality.py            # Task 6 — pure computation
│   ├── follow_through.py           # Task 7 — async 5-min probe
│   └── pipeline.py                 # Task 8 — orchestrator
│
├── db/
│   └── schema.sql                  # Modify — add filter_results + runner_scores
│
└── tests/
    ├── unit/
    │   ├── test_filter_base.py
    │   ├── test_rug_gate.py
    │   ├── test_holder_filter.py
    │   ├── test_insider_filter.py
    │   ├── test_entry_quality.py
    │   ├── test_follow_through.py
    │   └── test_filter_pipeline.py
    ├── integration/
    │   └── test_enrichment_to_filters.py
    └── fixtures/
        ├── rugcheck_report_summary.json    # Task 3
        ├── das_getTokenAccounts.json       # Task 4
        └── rugcheck_insiders_graph.json    # Task 5
```

**Files modified:**

```
meme-trading/runner/
├── db/schema.sql       # Add filter_results + runner_scores tables
└── main.py             # Task 9 — wire filter pipeline
```

---

## The FilterResult Contract (used by Tasks 3-7)

Every filter returns one `FilterResult`:

```python
@dataclass(frozen=True, eq=False)
class FilterResult:
    filter_name: str                    # "rug_gate", "holder_filter", etc.
    passed: bool                        # False only on hard gate failure
    hard_fail_reason: str | None        # set iff passed=False
    sub_scores: dict[str, float]        # e.g. {"rug_risk": 88}
    evidence: dict[str, Any]            # raw data JSON-serializable
```

Hard gate failures short-circuit the pipeline. Non-gate filters (like `EntryQualityFilter`) always return `passed=True` with sub-scores.

Sub-score namespacing per filter:
- `RugGate` → `sub_scores["rug_risk"]`
- `HolderFilter` → `sub_scores["holder_quality"]`
- `InsiderFilter` → `sub_scores["insider_risk"]` (Plan 2c combines with `rug_risk`)
- `EntryQualityFilter` → `sub_scores["entry_quality"]`
- `FollowThroughProbe` → `sub_scores["follow_through"]`

Cluster-derived scores (`wallet_quality`, `cluster_quality`) come from `EnrichedToken.cluster_signal` and are computed by Plan 2c's `FactorScorer` — not by filters.

---

## Task 1: FilterResult + FilteredCandidate + BaseFilter

**Why:** Lock the interface that every filter in Tasks 3-7 targets. Getting this right first prevents drift later.

**Files:**
- Create: `filters/__init__.py` (empty)
- Create: `filters/base.py`
- Create: `tests/unit/test_filter_base.py`

- [ ] **Step 1: Create empty filters/__init__.py**

Empty file at `meme-trading/runner/filters/__init__.py`.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_filter_base.py`:

```python
"""FilterResult, FilteredCandidate, and BaseFilter contract tests."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult


def _enriched(mint="MINT") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


def test_filter_result_pass_has_no_hard_fail_reason():
    r = FilterResult(
        filter_name="test",
        passed=True,
        hard_fail_reason=None,
        sub_scores={"score": 80.0},
        evidence={"key": "value"},
    )
    assert r.passed is True
    assert r.hard_fail_reason is None
    assert r.sub_scores["score"] == 80.0


def test_filter_result_hard_fail_has_reason():
    r = FilterResult(
        filter_name="rug_gate",
        passed=False,
        hard_fail_reason="mint authority not revoked",
        sub_scores={},
        evidence={"mint_authority": "SomeAddr"},
    )
    assert r.passed is False
    assert r.hard_fail_reason == "mint authority not revoked"


def test_filter_result_is_frozen():
    import dataclasses
    r = FilterResult(
        filter_name="t",
        passed=True,
        hard_fail_reason=None,
        sub_scores={},
        evidence={},
    )
    try:
        r.passed = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("FilterResult must be frozen")


def test_filtered_candidate_carries_enriched_and_results():
    enriched = _enriched()
    results = [
        FilterResult("a", True, None, {"x": 50.0}, {}),
        FilterResult("b", True, None, {"y": 60.0}, {}),
    ]
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=results,
        gate_passed=True,
        hard_fail_reason=None,
    )
    assert fc.enriched.token_mint == "MINT"
    assert len(fc.filter_results) == 2
    assert fc.gate_passed is True


def test_filtered_candidate_hard_fail_shortcircuit():
    enriched = _enriched()
    fail = FilterResult(
        filter_name="rug_gate",
        passed=False,
        hard_fail_reason="lp not locked",
        sub_scores={},
        evidence={"lp_locked_pct": 30},
    )
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=[fail],
        gate_passed=False,
        hard_fail_reason="lp not locked",
    )
    assert fc.gate_passed is False
    assert fc.hard_fail_reason == "lp not locked"


@pytest.mark.asyncio
async def test_base_filter_apply_is_abstract():
    class Incomplete(BaseFilter):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_base_filter_concrete_subclass_works():
    class Stub(BaseFilter):
        name = "stub"

        async def apply(self, enriched: EnrichedToken) -> FilterResult:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"stub_score": 100.0},
                evidence={"mint": enriched.token_mint},
            )

    stub = Stub()
    result = await stub.apply(_enriched())
    assert result.filter_name == "stub"
    assert result.sub_scores["stub_score"] == 100.0
    assert result.evidence["mint"] == "MINT"
```

- [ ] **Step 3: Run failing test**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/unit/test_filter_base.py -v
```

Expected: `ModuleNotFoundError: No module named 'runner.filters.base'`.

- [ ] **Step 4: Implement filters/base.py**

Create `filters/base.py`:

```python
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
```

- [ ] **Step 5: Run tests — expect all passing**

```bash
python -m pytest tests/unit/test_filter_base.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 88 passed (81 prior + 7 new).

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/__init__.py meme-trading/runner/filters/base.py meme-trading/runner/tests/unit/test_filter_base.py
git commit -m "runner: FilterResult, FilteredCandidate, BaseFilter contracts"
git push
```

---

## Task 2: Schema migration — filter_results + runner_scores tables

**Why:** The filter pipeline needs a place to persist every filter's verdict for audit/debug, and the scoring engine in Plan 2c needs `runner_scores`. Adding both tables in the same migration keeps the schema atomic.

**Files:**
- Modify: `db/schema.sql` (append two new CREATE TABLE statements)
- Create: `tests/unit/test_filter_results_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_filter_results_schema.py`:

```python
"""Schema for filter_results and runner_scores tables."""
import pytest

from runner.db.database import Database


@pytest.mark.asyncio
async def test_filter_results_table_exists_with_correct_columns(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA table_info(filter_results)") as cur:
        rows = await cur.fetchall()
    columns = {r[1]: r[2] for r in rows}  # name -> type

    assert "id" in columns
    assert "token_mint" in columns
    assert "filter_name" in columns
    assert "passed" in columns
    assert "hard_fail_reason" in columns
    assert "sub_scores_json" in columns
    assert "evidence_json" in columns
    assert "cluster_signal_id" in columns
    assert "created_at" in columns

    await db.close()


@pytest.mark.asyncio
async def test_runner_scores_table_exists_with_correct_columns(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    async with db.conn.execute("PRAGMA table_info(runner_scores)") as cur:
        rows = await cur.fetchall()
    columns = {r[1]: r[2] for r in rows}

    assert "id" in columns
    assert "token_mint" in columns
    assert "cluster_signal_id" in columns
    assert "runner_score" in columns
    assert "verdict" in columns
    assert "sub_scores_json" in columns
    assert "explanation_json" in columns
    assert "created_at" in columns

    await db.close()


@pytest.mark.asyncio
async def test_can_insert_and_query_filter_result(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await db.conn.execute(
        """
        INSERT INTO filter_results
        (token_mint, filter_name, passed, hard_fail_reason,
         sub_scores_json, evidence_json, cluster_signal_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("MINT1", "rug_gate", 1, None, '{"rug_risk": 88}', '{}', 42),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT token_mint, filter_name, passed FROM filter_results"
    ) as cur:
        rows = await cur.fetchall()
    assert rows == [("MINT1", "rug_gate", 1)]

    await db.close()
```

- [ ] **Step 2: Run failing test**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/unit/test_filter_results_schema.py -v
```

Expected: assertions fail because the new tables don't exist yet.

- [ ] **Step 3: Modify db/schema.sql**

Open `meme-trading/runner/db/schema.sql`. Append these blocks at the end, BEFORE the `schema_version` block (so schema_version stays as the final marker):

```sql

-- Filter pipeline results — one row per (candidate, filter) pair.
CREATE TABLE IF NOT EXISTS filter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    filter_name TEXT NOT NULL,
    passed INTEGER NOT NULL,
    hard_fail_reason TEXT,
    sub_scores_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    cluster_signal_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_filter_results_mint ON filter_results(token_mint);
CREATE INDEX IF NOT EXISTS idx_filter_results_cluster ON filter_results(cluster_signal_id);

-- Final Runner Score + Verdict — one row per candidate (populated by scoring engine in Plan 2c).
CREATE TABLE IF NOT EXISTS runner_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    cluster_signal_id INTEGER,
    runner_score REAL NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('ignore', 'watch', 'strong_candidate', 'probable_runner')),
    sub_scores_json TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_runner_scores_mint ON runner_scores(token_mint);
CREATE INDEX IF NOT EXISTS idx_runner_scores_verdict ON runner_scores(verdict);
CREATE INDEX IF NOT EXISTS idx_runner_scores_time ON runner_scores(created_at);
```

Make sure the existing `schema_version` block stays AFTER these (and bump the inserted version if you want, though idempotent bootstrap via `INSERT OR IGNORE` means you don't have to).

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_filter_results_schema.py tests/unit/test_database.py -v
```

Expected: 3 new tests pass + all 4 original `test_database` tests still pass.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 91 passed (88 prior + 3 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/db/schema.sql meme-trading/runner/tests/unit/test_filter_results_schema.py
git commit -m "runner: schema — filter_results and runner_scores tables"
git push
```

---

## Task 3: RugGate filter

**Why:** First and most important filter. Uses RugCheck `/v1/tokens/{mint}/report/summary` (free, no auth) to check mint authority, freeze authority, LP lock %, and overall rug score. Hard gates fail here; sub-score feeds `rug_risk`.

**Files:**
- Create: `filters/rug_gate.py`
- Create: `tests/fixtures/rugcheck_report_summary.json`
- Create: `tests/unit/test_rug_gate.py`

- [ ] **Step 1: Create the RugCheck fixture**

Create `tests/fixtures/rugcheck_report_summary.json`:

```json
{
  "score": 120,
  "score_normalised": 12,
  "lpLockedPct": 95.0,
  "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "tokenType": "standard",
  "risks": [
    {
      "name": "Low Holder Count",
      "level": "warn",
      "description": "Token has fewer than 100 holders",
      "score": 5,
      "value": "42"
    }
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_rug_gate.py`:

```python
"""RugGate filter — RugCheck /report/summary-based hard gates + rug_risk sub-score."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.rug_gate import RugGate
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "rugcheck_report_summary.json"


def _enriched(mint="TestMint1111111111111111111111111111111111") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
        mint_authority=None,
        freeze_authority=None,
    )


@pytest.mark.asyncio
async def test_passes_when_lp_locked_and_no_hard_risks():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        result = await gate.apply(_enriched())

    assert result.filter_name == "rug_gate"
    assert result.passed is True
    assert result.hard_fail_reason is None
    # score_normalised=12, one warn risk (-5) → 100 - 12 - 5 = 83
    assert result.sub_scores["rug_risk"] == pytest.approx(83, abs=1)
    assert result.evidence["lp_locked_pct"] == pytest.approx(95.0)
    await client.aclose()


@pytest.mark.asyncio
async def test_hard_fails_when_lp_locked_below_threshold():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())
    payload["lpLockedPct"] = 40.0  # below threshold

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        result = await gate.apply(_enriched())

    assert result.passed is False
    assert "lp" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_hard_fails_when_mint_authority_still_enabled():
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(200, json=payload))

        # EnrichedToken with non-None mint_authority → hard fail on that check
        base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
        sig = ClusterSignal(
            token_mint="TestMint1111111111111111111111111111111111",
            wallets=["A1", "A2", "B1"],
            wallet_count=3,
            tier_counts={"A": 2, "B": 1},
            first_buy_time=base,
            last_buy_time=base + timedelta(minutes=10),
            convergence_seconds=600,
            mid_price_sol=0.00025,
        )
        enriched = EnrichedToken(
            token_mint="TestMint1111111111111111111111111111111111",
            cluster_signal=sig,
            enriched_at=base + timedelta(minutes=11),
            mint_authority="SomeAuth111111111111111111111111111111",  # NOT revoked
            freeze_authority=None,
        )

        result = await gate.apply(enriched)

    assert result.passed is False
    assert "mint" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_passes_with_zero_score_when_rugcheck_fails():
    """API failure should not hard-fail; gate degrades to low sub-score but passes."""
    client = RateLimitedClient(default_rps=100)
    gate = RugGate(client, lp_locked_pct_min=85)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get(
            "/v1/tokens/TestMint1111111111111111111111111111111111/report/summary"
        ).mock(return_value=httpx.Response(500, json={}))

        result = await gate.apply(_enriched())

    # API failure shouldn't hard-fail — operator decides upstream. Mark as
    # degraded and let the rug_risk sub-score go to 0.
    assert result.passed is True
    assert result.sub_scores["rug_risk"] == pytest.approx(0, abs=1)
    assert "rugcheck_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
```

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/unit/test_rug_gate.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement filters/rug_gate.py**

Create `filters/rug_gate.py`:

```python
"""RugGate filter — RugCheck /report/summary-based hard gates + rug_risk sub-score."""
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.rug_gate")

RUGCHECK_BASE = "https://api.rugcheck.xyz"


class RugGate(BaseFilter):
    """Checks RugCheck report summary + EnrichedToken authority fields.

    Hard gates (any failure → passed=False):
      1. Mint authority must be revoked (EnrichedToken.mint_authority is None)
      2. Freeze authority must be revoked (EnrichedToken.freeze_authority is None)
      3. LP locked % must be >= lp_locked_pct_min

    Sub-score: `rug_risk` starts at 100, subtracts RugCheck score_normalised
    directly, subtracts 5 per `warn` risk entry (cap -30 on risks alone).
    API failures degrade rug_risk to 0 but do NOT hard-fail the gate
    (operator gets a logged warning).
    """

    name = "rug_gate"

    def __init__(
        self,
        http: RateLimitedClient,
        lp_locked_pct_min: float = 85.0,
    ):
        self.http = http
        self.lp_locked_pct_min = lp_locked_pct_min

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        # Check authority fields first — these come from Plan 2a metadata fetch
        if enriched.mint_authority is not None:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason="mint authority not revoked",
                sub_scores={"rug_risk": 0.0},
                evidence={"mint_authority": enriched.mint_authority},
            )
        if enriched.freeze_authority is not None:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason="freeze authority not revoked",
                sub_scores={"rug_risk": 0.0},
                evidence={"freeze_authority": enriched.freeze_authority},
            )

        # Fetch RugCheck summary
        summary = await self._fetch_summary(enriched.token_mint)
        if summary is None:
            return FilterResult(
                filter_name=self.name,
                passed=True,  # API failure is not a hard fail
                hard_fail_reason=None,
                sub_scores={"rug_risk": 0.0},
                evidence={"errors": ["rugcheck_api_unavailable"]},
            )

        lp_locked_pct = float(summary.get("lpLockedPct") or 0.0)
        if lp_locked_pct < self.lp_locked_pct_min:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason=f"lp locked pct {lp_locked_pct:.1f} below min {self.lp_locked_pct_min}",
                sub_scores={"rug_risk": 0.0},
                evidence={"lp_locked_pct": lp_locked_pct},
            )

        # Compute rug_risk sub-score
        score = 100.0
        score_normalised = float(summary.get("score_normalised") or 0.0)
        score -= score_normalised

        risks = summary.get("risks") or []
        warn_risks = [r for r in risks if r.get("level") == "warn"]
        penalty = min(5.0 * len(warn_risks), 30.0)
        score -= penalty

        score = max(0.0, min(100.0, score))

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"rug_risk": score},
            evidence={
                "lp_locked_pct": lp_locked_pct,
                "rugcheck_score_normalised": score_normalised,
                "warn_risks": [r.get("name") for r in warn_risks],
                "risk_count": len(risks),
            },
        )

    async def _fetch_summary(self, mint: str) -> dict[str, Any] | None:
        url = f"{RUGCHECK_BASE}/v1/tokens/{mint}/report/summary"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("rugcheck_summary_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            logger.warning(
                "rugcheck_summary_non_200", mint=mint, status=resp.status_code
            )
            return None
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("rugcheck_summary_bad_json", mint=mint, error=str(e))
            return None
```

- [ ] **Step 5: Run tests — expect 4 passed**

```bash
python -m pytest tests/unit/test_rug_gate.py -v
```

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 95 passed (91 prior + 4 new).

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/rug_gate.py meme-trading/runner/tests/fixtures/rugcheck_report_summary.json meme-trading/runner/tests/unit/test_rug_gate.py
git commit -m "runner: RugGate filter with RugCheck hard gates and rug_risk sub-score"
git push
```

---

## Task 4: HolderFilter

**Why:** Computes holder quality — unique count, top-10 concentration (excluding LP + deployer), contributes `holder_quality` sub-score. Hard gate on top-10 > 70%.

**Approach:** Helius DAS `getTokenAccounts` returns accounts sorted by balance. Pagination limited to first 1000 holders for v1. Identify LP account by its presence in `cluster_signal.wallets` (no — wrong; LP is a program-owned pool). For v1, we can't reliably distinguish LP holders without extra RPC calls; we exclude the `deployer_address` (from `EnrichedToken.deployer_address`) and treat the rest as "holders."

**Files:**
- Create: `filters/holder_filter.py`
- Create: `tests/fixtures/das_getTokenAccounts.json`
- Create: `tests/unit/test_holder_filter.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/das_getTokenAccounts.json`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "total": 7,
    "limit": 1000,
    "token_accounts": [
      {"address": "Ata1", "mint": "M", "owner": "Holder1", "amount": 50000000000, "frozen": false},
      {"address": "Ata2", "mint": "M", "owner": "Holder2", "amount": 30000000000, "frozen": false},
      {"address": "Ata3", "mint": "M", "owner": "Holder3", "amount": 8000000000, "frozen": false},
      {"address": "Ata4", "mint": "M", "owner": "Holder4", "amount": 5000000000, "frozen": false},
      {"address": "Ata5", "mint": "M", "owner": "Holder5", "amount": 3000000000, "frozen": false},
      {"address": "Ata6", "mint": "M", "owner": "Holder6", "amount": 2000000000, "frozen": false},
      {"address": "Ata7", "mint": "M", "owner": "DeployerWallet", "amount": 2000000000, "frozen": false}
    ]
  }
}
```

Total supply = 100_000_000_000. Top-10 excluding DeployerWallet: 50+30+8+5+3+2 = 98% of supply. Will HARD FAIL the 70% top-10 gate.

For a passing test, use a different payload.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_holder_filter.py`:

```python
"""HolderFilter — Helius DAS getTokenAccounts with top-10 concentration gate."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.holder_filter import HolderFilter
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "das_getTokenAccounts.json"


def _enriched(deployer: str | None = "DeployerWallet") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
        mint_authority=None,
        freeze_authority=None,
        deployer_address=deployer,
    )


@pytest.mark.asyncio
async def test_hard_fails_when_top10_over_70_pct():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched())

    # Top-10 excluding deployer = 98% — hard fail
    assert result.passed is False
    assert "top" in result.hard_fail_reason.lower()
    await client.aclose()


@pytest.mark.asyncio
async def test_passes_with_good_holder_distribution():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    # Distributed supply — no single holder > 10%, top-10 sums to < 70%
    payload = {
        "jsonrpc": "2.0",
        "result": {
            "total": 60,
            "token_accounts": [
                # Top 10 (60% of supply total)
                {"address": f"A{i}", "mint": "M", "owner": f"Holder{i}",
                 "amount": 6_000_000_000, "frozen": False}
                for i in range(10)
            ] + [
                # 50 smaller holders, 40% of supply split
                {"address": f"B{i}", "mint": "M", "owner": f"Small{i}",
                 "amount": 800_000_000, "frozen": False}
                for i in range(50)
            ]
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched(deployer=None))

    assert result.passed is True
    assert result.sub_scores["holder_quality"] > 0
    assert result.evidence["unique_holders"] == 60
    assert result.evidence["top10_pct"] == pytest.approx(60.0, abs=0.5)
    await client.aclose()


@pytest.mark.asyncio
async def test_holder_quality_score_scales_with_count_and_concentration():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    # 100 holders, top-10 = 35% — should score well
    payload = {
        "jsonrpc": "2.0",
        "result": {
            "total": 100,
            "token_accounts": [
                {"address": f"A{i}", "mint": "M", "owner": f"H{i}",
                 "amount": 3_500_000_000, "frozen": False}
                for i in range(10)
            ] + [
                {"address": f"B{i}", "mint": "M", "owner": f"S{i}",
                 "amount": 722_222_222, "frozen": False}
                for i in range(90)
            ]
        }
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        result = await filt.apply(_enriched(deployer=None))

    assert result.passed is True
    # With > 100 holders AND top-10 30-45% concentration → ~50 points
    assert 40 <= result.sub_scores["holder_quality"] <= 80
    await client.aclose()


@pytest.mark.asyncio
async def test_api_failure_returns_pass_with_zero_subscore():
    client = RateLimitedClient(default_rps=100)
    filt = HolderFilter(client, rpc_url="https://rpc.helius.test/rpc", top10_max_pct=70)

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(500, json={}))
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["holder_quality"] == 0.0
    assert "das_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
```

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/unit/test_holder_filter.py -v
```

- [ ] **Step 4: Implement filters/holder_filter.py**

Create `filters/holder_filter.py`:

```python
"""HolderFilter — Helius DAS getTokenAccounts with top-10 concentration gate."""
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.holder_filter")


class HolderFilter(BaseFilter):
    """Computes holder count and top-10 concentration.

    Hard gate: top-10 holder concentration (excluding the deployer address,
    if known) > top10_max_pct is a hard fail (typically 70%).

    Sub-score `holder_quality` (0-100):
      + 30 for > 100 unique holders, + 20 for 50-100, + 10 for 20-50
      + 30 for top-10 < 30%, + 20 for 30-45%, + 10 for 45-60%, + 0 for >= 60%
    (max 100 — two 30s and an excess 20 equal 80; we cap.)
    """

    name = "holder_filter"

    def __init__(
        self,
        http: RateLimitedClient,
        rpc_url: str,
        top10_max_pct: float = 70.0,
    ):
        self.http = http
        self.rpc_url = rpc_url
        self.top10_max_pct = top10_max_pct

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        accounts = await self._fetch_token_accounts(enriched.token_mint)
        if accounts is None:
            return FilterResult(
                filter_name=self.name,
                passed=True,  # API failure is not a hard fail
                hard_fail_reason=None,
                sub_scores={"holder_quality": 0.0},
                evidence={"errors": ["das_api_unavailable"]},
            )

        # Exclude deployer from holder set (common for dev-owned supply).
        deployer = enriched.deployer_address
        filtered = [
            a for a in accounts
            if a.get("owner") and a.get("owner") != deployer
        ]

        total_supply = sum(int(a.get("amount") or 0) for a in filtered)
        if total_supply == 0:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"holder_quality": 0.0},
                evidence={"unique_holders": 0, "top10_pct": 0.0},
            )

        # Sort by balance descending
        filtered.sort(key=lambda a: int(a.get("amount") or 0), reverse=True)
        unique_holders = len({a.get("owner") for a in filtered})

        top10 = filtered[:10]
        top10_balance = sum(int(a.get("amount") or 0) for a in top10)
        top10_pct = (top10_balance / total_supply) * 100.0

        if top10_pct > self.top10_max_pct:
            return FilterResult(
                filter_name=self.name,
                passed=False,
                hard_fail_reason=f"top-10 holders hold {top10_pct:.1f}% > {self.top10_max_pct}%",
                sub_scores={"holder_quality": 0.0},
                evidence={
                    "unique_holders": unique_holders,
                    "top10_pct": top10_pct,
                },
            )

        score = 0.0
        if unique_holders > 100:
            score += 30
        elif unique_holders >= 50:
            score += 20
        elif unique_holders >= 20:
            score += 10

        if top10_pct < 30:
            score += 30
        elif top10_pct < 45:
            score += 20
        elif top10_pct < 60:
            score += 10

        score = min(100.0, score)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"holder_quality": score},
            evidence={
                "unique_holders": unique_holders,
                "top10_pct": top10_pct,
                "total_supply": total_supply,
            },
        )

    async def _fetch_token_accounts(self, mint: str) -> list[dict] | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccounts",
                    "params": {"mint": mint, "limit": 1000},
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getTokenAccounts_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None

        result = body.get("result")
        if not result or not isinstance(result, dict):
            return None
        accounts = result.get("token_accounts") or []
        return accounts if isinstance(accounts, list) else None
```

- [ ] **Step 5: Run tests — expect 4 passed**

```bash
python -m pytest tests/unit/test_holder_filter.py -v
```

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 99 passed (95 prior + 4 new).

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/holder_filter.py meme-trading/runner/tests/fixtures/das_getTokenAccounts.json meme-trading/runner/tests/unit/test_holder_filter.py
git commit -m "runner: HolderFilter with top-10 concentration gate and holder_quality sub-score"
git push
```

---

## Task 5: InsiderFilter

**Why:** Uses RugCheck `/v1/tokens/{mint}/insiders/graph` (free, no auth) to detect bundled/linked wallets. Produces `insider_risk` sub-score — Plan 2c combines this with `rug_risk`.

**Files:**
- Create: `filters/insider_filter.py`
- Create: `tests/fixtures/rugcheck_insiders_graph.json`
- Create: `tests/unit/test_insider_filter.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/rugcheck_insiders_graph.json`:

```json
{
  "nodes": [
    {"id": "Insider1", "type": "wallet", "balance": 15000000000},
    {"id": "Insider2", "type": "wallet", "balance": 12000000000},
    {"id": "Insider3", "type": "wallet", "balance": 8000000000},
    {"id": "Insider4", "type": "wallet", "balance": 3000000000}
  ],
  "edges": [
    {"source": "Insider1", "target": "Insider2"},
    {"source": "Insider2", "target": "Insider3"}
  ]
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_insider_filter.py`:

```python
"""InsiderFilter — RugCheck /insiders/graph insider count → insider_risk sub-score."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.insider_filter import InsiderFilter
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "rugcheck_insiders_graph.json"


def _enriched(mint="M") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


@pytest.mark.asyncio
async def test_scores_full_when_no_insiders():
    client = RateLimitedClient(default_rps=100)
    filt = InsiderFilter(client)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(200, json={"nodes": [], "edges": []})
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(100.0)
    assert result.evidence["insider_count"] == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_penalizes_four_insiders():
    """4 insiders lands in the 3-5 band: -15 → 85."""
    client = RateLimitedClient(default_rps=100)
    filt = InsiderFilter(client)

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(200, json=payload)
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(85.0)
    assert result.evidence["insider_count"] == 4
    await client.aclose()


@pytest.mark.asyncio
async def test_api_failure_returns_zero_subscore():
    client = RateLimitedClient(default_rps=100)
    filt = InsiderFilter(client)

    with respx.mock(base_url="https://api.rugcheck.xyz") as mock:
        mock.get("/v1/tokens/M/insiders/graph").mock(
            return_value=httpx.Response(500, json={})
        )
        result = await filt.apply(_enriched())

    assert result.passed is True
    assert result.sub_scores["insider_risk"] == pytest.approx(0.0)
    assert "insiders_api_unavailable" in result.evidence.get("errors", [])
    await client.aclose()
```

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/unit/test_insider_filter.py -v
```

- [ ] **Step 4: Implement filters/insider_filter.py**

Create `filters/insider_filter.py`:

```python
"""InsiderFilter — RugCheck /insiders/graph insider count → insider_risk sub-score.

Score bands (per runner spec):
  0-2 insiders: 100
  3-5:           85  (-15)
  6-10:          70  (-30)
  11+:           50  (-50, approaches a hard fail)
"""
from typing import Any

from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.insider_filter")

RUGCHECK_BASE = "https://api.rugcheck.xyz"


class InsiderFilter(BaseFilter):
    """Counts insider/linked wallets from RugCheck graph endpoint."""

    name = "insider_filter"

    def __init__(self, http: RateLimitedClient):
        self.http = http

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        graph = await self._fetch_graph(enriched.token_mint)
        if graph is None:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"insider_risk": 0.0},
                evidence={"errors": ["insiders_api_unavailable"]},
            )

        nodes = graph.get("nodes") or []
        count = len(nodes)

        if count <= 2:
            score = 100.0
        elif count <= 5:
            score = 85.0
        elif count <= 10:
            score = 70.0
        else:
            score = 50.0

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"insider_risk": score},
            evidence={
                "insider_count": count,
                "edge_count": len(graph.get("edges") or []),
            },
        )

    async def _fetch_graph(self, mint: str) -> dict[str, Any] | None:
        url = f"{RUGCHECK_BASE}/v1/tokens/{mint}/insiders/graph"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("insiders_graph_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except Exception:
            return None
```

- [ ] **Step 5: Run tests — expect 3 passed**

```bash
python -m pytest tests/unit/test_insider_filter.py -v
```

- [ ] **Step 6: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 102 passed (99 prior + 3 new).

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/insider_filter.py meme-trading/runner/tests/fixtures/rugcheck_insiders_graph.json meme-trading/runner/tests/unit/test_insider_filter.py
git commit -m "runner: InsiderFilter with RugCheck insiders graph insider_risk sub-score"
git push
```

---

## Task 6: EntryQualityFilter

**Why:** Pure computation — no HTTP calls. Reads `EnrichedToken` fields (price_sol, pair_age_seconds, slippage_at_size_pct) and the cluster signal's `mid_price_sol`. Produces `entry_quality` sub-score based on price extension, token age, and liquidity depth.

**Files:**
- Create: `filters/entry_quality.py`
- Create: `tests/unit/test_entry_quality.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_entry_quality.py`:

```python
"""EntryQualityFilter — pure computation of entry quality from EnrichedToken."""
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.entry_quality import EntryQualityFilter


def _enriched(
    mid_price=0.0001,
    current_price=0.0001,
    pair_age_seconds=600,
    slippage_25=1.0,
) -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=mid_price,
    )
    return EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
        price_sol=current_price,
        pair_age_seconds=pair_age_seconds,
        slippage_at_size_pct={0.25: slippage_25},
    )


@pytest.mark.asyncio
async def test_fresh_token_low_extension_scores_high():
    filt = EntryQualityFilter()

    # 0% extension, 10 min old, 1% slippage → near-perfect entry
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    # Base 100 (0% extension) + 15 (<30min) = 115 → cap 100
    assert result.sub_scores["entry_quality"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_extended_token_scores_low():
    filt = EntryQualityFilter()

    # 40% extension (30-60% band = 15 points), 2h old (0 mod), low slippage
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.00014,
        pair_age_seconds=2 * 3600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    assert result.sub_scores["entry_quality"] == pytest.approx(15.0, abs=1)


@pytest.mark.asyncio
async def test_stale_token_receives_penalty():
    filt = EntryQualityFilter()

    # 0% extension, 12h old → base 100 + (-10) = 90
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=12 * 3600,
        slippage_25=1.0,
    )
    result = await filt.apply(enriched)

    assert result.passed is True
    assert result.sub_scores["entry_quality"] == pytest.approx(90.0, abs=1)


@pytest.mark.asyncio
async def test_high_slippage_caps_score_at_40():
    filt = EntryQualityFilter()

    # 0% extension, fresh token, 7% slippage (>5%) → capped at 40
    enriched = _enriched(
        mid_price=0.0001,
        current_price=0.0001,
        pair_age_seconds=600,
        slippage_25=7.0,
    )
    result = await filt.apply(enriched)

    assert result.sub_scores["entry_quality"] <= 40.0


@pytest.mark.asyncio
async def test_missing_price_data_scores_zero():
    filt = EntryQualityFilter()

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="M",
        wallets=["A1"],
        wallet_count=1,
        tier_counts={"A": 1},
        first_buy_time=base,
        last_buy_time=base,
        convergence_seconds=0,
        mid_price_sol=0.0001,
    )
    enriched = EnrichedToken(
        token_mint="M",
        cluster_signal=sig,
        enriched_at=base,
        price_sol=None,  # price unavailable
    )
    result = await filt.apply(enriched)

    assert result.sub_scores["entry_quality"] == pytest.approx(0.0)
    assert "missing_current_price" in result.evidence.get("errors", [])
```

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/unit/test_entry_quality.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement filters/entry_quality.py**

Create `filters/entry_quality.py`:

```python
"""EntryQualityFilter — pure computation on EnrichedToken fields."""
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult


class EntryQualityFilter(BaseFilter):
    """Scores how good the entry point looks.

    Three factors:
    1. Price extension since cluster mid-price (anti-chase):
       < 5% → 100, 5-15% → 75, 15-30% → 45, 30-60% → 15, > 60% → 0
    2. Token freshness modifier (added to extension score):
       < 30m → +15, 30m-2h → +10, 2-6h → 0, 6-24h → -10, > 24h → -20
    3. Liquidity depth check: if 0.25 SOL slippage > 5%, cap score at 40.
    """

    name = "entry_quality"

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        current_price = enriched.price_sol
        cluster_price = enriched.cluster_signal.mid_price_sol

        if current_price is None or cluster_price is None or cluster_price <= 0:
            return FilterResult(
                filter_name=self.name,
                passed=True,
                hard_fail_reason=None,
                sub_scores={"entry_quality": 0.0},
                evidence={"errors": ["missing_current_price"]},
            )

        extension_pct = ((current_price - cluster_price) / cluster_price) * 100.0
        # Anti-chase: we care about upward extension only.
        if extension_pct < 5.0:
            score = 100.0
        elif extension_pct < 15.0:
            score = 75.0
        elif extension_pct < 30.0:
            score = 45.0
        elif extension_pct < 60.0:
            score = 15.0
        else:
            score = 0.0

        age_seconds = enriched.pair_age_seconds
        freshness_mod = 0.0
        if age_seconds is not None:
            if age_seconds < 30 * 60:
                freshness_mod = 15.0
            elif age_seconds < 2 * 3600:
                freshness_mod = 10.0
            elif age_seconds < 6 * 3600:
                freshness_mod = 0.0
            elif age_seconds < 24 * 3600:
                freshness_mod = -10.0
            else:
                freshness_mod = -20.0

        score = score + freshness_mod
        score = max(0.0, min(100.0, score))

        # Liquidity-depth cap
        slippage_025 = enriched.slippage_at_size_pct.get(0.25)
        if slippage_025 is not None and slippage_025 > 5.0:
            score = min(score, 40.0)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"entry_quality": score},
            evidence={
                "extension_pct": extension_pct,
                "pair_age_seconds": age_seconds,
                "slippage_25": slippage_025,
            },
        )
```

- [ ] **Step 4: Run tests — expect 5 passed**

```bash
python -m pytest tests/unit/test_entry_quality.py -v
```

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 107 passed (102 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/entry_quality.py meme-trading/runner/tests/unit/test_entry_quality.py
git commit -m "runner: EntryQualityFilter with extension, freshness, and liquidity depth scoring"
git push
```

---

## Task 7: FollowThroughProbe

**Why:** The hardest filter. Opens a 5-minute probe window, waits, then counts how many additional A+B wallets bought the mint during the window AND checks whether the price held. This is the "is the cluster expanding or dead?" test.

**Approach v1:**
- Sleep `probe_minutes * 60` seconds
- Query `buy_events` table for distinct wallets buying this mint with `block_time > cluster.last_buy_time`
- Cross-reference with `wallet_tiers` to get A+B wallets only (tier cache)
- Re-fetch current price via a `PriceLiquidityFetcher`
- Compute sub-score per the scoring model:
  - +3 A+B wallets: 100
  - +2: 80
  - +1: 60
  - 0 joined, price within -5% of entry: 40
  - 0 joined, price up > 10%: 70
  - Price dumps > 15%: 0

**Dependencies:** `Database`, `WalletTierCache`, `PriceLiquidityFetcher`.

**Files:**
- Create: `filters/follow_through.py`
- Create: `tests/unit/test_follow_through.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_follow_through.py`:

```python
"""FollowThroughProbe — async 5-minute probe with DB + price check."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.follow_through import FollowThroughProbe


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _enriched(mint="M", mid_price=0.0001) -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=mid_price,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


@pytest.mark.asyncio
async def test_probe_counts_new_ab_wallets_and_scores_high(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    # Seed buy_events: 2 new A+B wallets after cluster.last_buy_time
    base_last = datetime(2026, 4, 11, 10, 10, tzinfo=timezone.utc)
    for i, wallet in enumerate(["A3", "B2"]):
        await db.conn.execute(
            """
            INSERT INTO buy_events
            (signature, wallet_address, token_mint, sol_amount,
             token_amount, price_sol, block_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"sig{i}",
                wallet,
                "M",
                0.5,
                1000,
                0.0001,
                (base_last + timedelta(minutes=2 + i)).isoformat(),
            ),
        )
    await db.conn.commit()

    tier_cache = _StubTierCache({"A3": Tier.A, "B2": Tier.B})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.0001,  # flat price
        "price_usd": 0.0001,
        "liquidity_usd": 20000.0,
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,  # zero-delay for test
    )

    result = await probe.apply(_enriched())

    assert result.passed is True
    # +2 A+B wallets → score 80
    assert result.sub_scores["follow_through"] == pytest.approx(80.0)
    assert result.evidence["new_ab_wallets"] == 2

    await db.close()


@pytest.mark.asyncio
async def test_probe_no_new_wallets_price_up_scores_70(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.000115,  # +15%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(70.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_price_dump_scores_zero(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})
    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.00008,  # -20%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(0.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_no_new_wallets_price_flat_scores_40(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    tier_cache = _StubTierCache({})
    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {
        "price_sol": 0.000098,  # -2%
        "slippage_at_size_pct": {},
    }

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    assert result.sub_scores["follow_through"] == pytest.approx(40.0)

    await db.close()


@pytest.mark.asyncio
async def test_probe_c_tier_wallets_not_counted(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    base_last = datetime(2026, 4, 11, 10, 10, tzinfo=timezone.utc)
    for i, wallet in enumerate(["C1", "C2", "C3"]):
        await db.conn.execute(
            """
            INSERT INTO buy_events
            (signature, wallet_address, token_mint, sol_amount,
             token_amount, price_sol, block_time)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"sig{i}",
                wallet,
                "M",
                0.5,
                1000,
                0.0001,
                (base_last + timedelta(minutes=2 + i)).isoformat(),
            ),
        )
    await db.conn.commit()

    tier_cache = _StubTierCache({"C1": Tier.C, "C2": Tier.C, "C3": Tier.C})

    price_fetcher = AsyncMock()
    price_fetcher.fetch.return_value = {"price_sol": 0.0001}

    probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=0,
    )

    result = await probe.apply(_enriched())

    # C-tier wallets don't count → 0 new A+B + flat price → 40
    assert result.sub_scores["follow_through"] == pytest.approx(40.0)
    assert result.evidence["new_ab_wallets"] == 0

    await db.close()
```

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/unit/test_follow_through.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement filters/follow_through.py**

Create `filters/follow_through.py`:

```python
"""FollowThroughProbe — async 5-minute probe measuring cluster follow-through.

After a probe window, count additional A+B-tier wallets that bought
the same mint AND check whether price held. Score per the runner spec:

    +3 A+B wallets joined: 100
    +2:                     80
    +1:                     60
    0 joined, price within -5% of entry: 40
    0 joined, price up > 10%: 70
    Price dumps > 15%: 0 (dead cluster)
"""
import asyncio

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.follow_through")


class FollowThroughProbe(BaseFilter):
    """Wait, then count new A+B wallet buys and check price delta."""

    name = "follow_through"

    def __init__(
        self,
        db: Database,
        tier_cache: WalletTierCache,
        price_fetcher,
        probe_minutes: float = 5.0,
    ):
        self.db = db
        self.tier_cache = tier_cache
        self.price_fetcher = price_fetcher
        self.probe_minutes = probe_minutes

    async def apply(self, enriched: EnrichedToken) -> FilterResult:
        # Sleep for the probe window (probe_minutes=0 for tests).
        await asyncio.sleep(self.probe_minutes * 60.0)

        new_ab_wallets = await self._count_new_ab_wallets(enriched)
        current_price = await self._current_price(enriched.token_mint)

        cluster_price = enriched.cluster_signal.mid_price_sol
        price_delta_pct: float | None = None
        if current_price is not None and cluster_price > 0:
            price_delta_pct = ((current_price - cluster_price) / cluster_price) * 100.0

        score = self._score(new_ab_wallets, price_delta_pct)

        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores={"follow_through": score},
            evidence={
                "new_ab_wallets": new_ab_wallets,
                "price_delta_pct": price_delta_pct,
                "probe_minutes": self.probe_minutes,
            },
        )

    def _score(
        self, new_ab_wallets: int, price_delta_pct: float | None
    ) -> float:
        # Price dump hard-zero
        if price_delta_pct is not None and price_delta_pct < -15.0:
            return 0.0

        if new_ab_wallets >= 3:
            return 100.0
        if new_ab_wallets == 2:
            return 80.0
        if new_ab_wallets == 1:
            return 60.0

        # No new wallets — fall back to price action
        if price_delta_pct is None:
            return 40.0  # neutral when price unknown
        if price_delta_pct > 10.0:
            return 70.0
        if price_delta_pct >= -5.0:
            return 40.0
        return 20.0  # -5% to -15%, weakening

    async def _count_new_ab_wallets(self, enriched: EnrichedToken) -> int:
        assert self.db.conn is not None
        cutoff = enriched.cluster_signal.last_buy_time.isoformat()
        cluster_wallets = set(enriched.cluster_signal.wallets)

        async with self.db.conn.execute(
            """
            SELECT DISTINCT wallet_address FROM buy_events
            WHERE token_mint = ?
              AND block_time > ?
            """,
            (enriched.token_mint, cutoff),
        ) as cur:
            rows = await cur.fetchall()

        new_wallets = [
            w for (w,) in rows
            if w not in cluster_wallets
            and self.tier_cache.tier_of(w) in (Tier.A, Tier.B)
        ]
        return len(new_wallets)

    async def _current_price(self, mint: str) -> float | None:
        try:
            result = await self.price_fetcher.fetch(mint)
        except Exception as e:  # noqa: BLE001
            logger.warning("follow_through_price_error", mint=mint, error=str(e))
            return None
        if result is None:
            return None
        return result.get("price_sol")
```

- [ ] **Step 4: Run tests — expect 5 passed**

```bash
python -m pytest tests/unit/test_follow_through.py -v
```

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 112 passed (107 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/follow_through.py meme-trading/runner/tests/unit/test_follow_through.py
git commit -m "runner: FollowThroughProbe with 5min async probe and follow_through sub-score"
git push
```

---

## Task 8: FilterPipeline orchestrator

**Why:** Runs the 5 filters in a controlled order with hard-gate short-circuiting. Spawns a task per EnrichedToken so 5-minute probes don't block other candidates. Persists all FilterResults to the `filter_results` table. Emits `FilteredCandidate` on a new `filter_results_bus`.

**Files:**
- Create: `filters/pipeline.py`
- Create: `tests/unit/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_filter_pipeline.py`:

```python
"""FilterPipeline orchestrator — spawns per-candidate tasks, persists results."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.filters.pipeline import FilterPipeline


def _enriched(mint="M") -> EnrichedToken:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )
    return EnrichedToken(
        token_mint=mint,
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=11),
    )


class _StubFilter(BaseFilter):
    def __init__(self, name: str, result: FilterResult):
        self.name = name  # type: ignore[misc]
        self._result = result

    async def apply(self, enriched):
        return self._result


@pytest.mark.asyncio
async def test_pipeline_runs_all_filters_on_pass(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("f1", FilterResult("f1", True, None, {"a": 50}, {})),
        _StubFilter("f2", FilterResult("f2", True, None, {"b": 60}, {})),
        _StubFilter("f3", FilterResult("f3", True, None, {"c": 70}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    # Call _process_one directly for synchronous verification
    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert fc.gate_passed is True
    assert len(fc.filter_results) == 3
    names = [r.filter_name for r in fc.filter_results]
    assert names == ["f1", "f2", "f3"]

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_short_circuits_on_hard_fail(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("f1", FilterResult("f1", True, None, {"a": 50}, {})),
        _StubFilter("f2", FilterResult("f2", False, "bad thing", {}, {})),
        _StubFilter("f3", FilterResult("f3", True, None, {"c": 70}, {})),  # should not run
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert fc.gate_passed is False
    assert fc.hard_fail_reason == "bad thing"
    assert len(fc.filter_results) == 2  # f1 + f2
    assert fc.filter_results[-1].filter_name == "f2"

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_runs_probe_after_sync_filters(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    sync_filters = [
        _StubFilter("rug", FilterResult("rug", True, None, {"rug_risk": 90}, {})),
    ]
    probe = _StubFilter(
        "follow_through",
        FilterResult("follow_through", True, None, {"follow_through": 100}, {}),
    )

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=sync_filters,
        probe_filter=probe,
        db=db,
    )

    await pipeline._process_one(_enriched())

    fc: FilteredCandidate = filtered_bus.get_nowait()
    assert len(fc.filter_results) == 2
    assert fc.filter_results[0].filter_name == "rug"
    assert fc.filter_results[1].filter_name == "follow_through"

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_skips_probe_on_hard_fail(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    sync_filters = [
        _StubFilter("rug", FilterResult("rug", False, "rug fail", {}, {})),
    ]
    probe_called = False

    class _ProbeSpy(BaseFilter):
        name = "probe_spy"

        async def apply(self, enriched):
            nonlocal probe_called
            probe_called = True
            return FilterResult("probe_spy", True, None, {}, {})

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=sync_filters,
        probe_filter=_ProbeSpy(),
        db=db,
    )

    await pipeline._process_one(_enriched())

    assert probe_called is False
    fc = filtered_bus.get_nowait()
    assert fc.gate_passed is False

    await db.close()


@pytest.mark.asyncio
async def test_pipeline_persists_filter_results_to_db(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug", FilterResult("rug", True, None, {"rug_risk": 88}, {"ev": 1})),
        _StubFilter("holder", FilterResult("holder", True, None, {"holder_quality": 70}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=filters,
        probe_filter=None,
        db=db,
    )

    await pipeline._process_one(_enriched())

    async with db.conn.execute(
        "SELECT filter_name, passed, sub_scores_json FROM filter_results ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 2
    assert rows[0][0] == "rug"
    assert json.loads(rows[0][2]) == {"rug_risk": 88}
    assert rows[1][0] == "holder"

    await db.close()
```

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/unit/test_filter_pipeline.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement filters/pipeline.py**

Create `filters/pipeline.py`:

```python
"""FilterPipeline orchestrator.

Consumes EnrichedToken from `enriched_bus`, runs the sync filters in order,
short-circuits on any hard-gate failure, then runs the (async) probe filter
on survivors. Each candidate runs as its own asyncio task so slow probes
do not block other candidates. Persists all FilterResults to the
`filter_results` table. Emits FilteredCandidate on `filtered_bus`.
"""
import asyncio
import json

from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.utils.logging import get_logger

logger = get_logger("runner.filters.pipeline")


class FilterPipeline:
    def __init__(
        self,
        enriched_bus: asyncio.Queue,
        filtered_bus: asyncio.Queue,
        sync_filters: list[BaseFilter],
        probe_filter: BaseFilter | None,
        db: Database | None = None,
    ):
        self.enriched_bus = enriched_bus
        self.filtered_bus = filtered_bus
        self.sync_filters = sync_filters
        self.probe_filter = probe_filter
        self.db = db

    async def run(self) -> None:
        logger.info(
            "filter_pipeline_start",
            sync_filters=[f.name for f in self.sync_filters],
            probe_filter=self.probe_filter.name if self.probe_filter else None,
        )
        while True:
            enriched: EnrichedToken = await self.enriched_bus.get()
            # Spawn a per-candidate task so probes don't block other candidates.
            asyncio.create_task(self._process_one(enriched))

    async def _process_one(self, enriched: EnrichedToken) -> None:
        results: list[FilterResult] = []
        gate_passed = True
        hard_fail_reason: str | None = None

        for f in self.sync_filters:
            try:
                result = await f.apply(enriched)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "filter_crashed",
                    filter_name=f.name,
                    mint=enriched.token_mint,
                    error=str(e),
                )
                result = FilterResult(
                    filter_name=f.name,
                    passed=False,
                    hard_fail_reason=f"filter_crashed: {e}",
                    sub_scores={},
                    evidence={},
                )
            results.append(result)
            if not result.passed:
                gate_passed = False
                hard_fail_reason = result.hard_fail_reason
                break

        # Only run probe if all sync filters passed.
        if gate_passed and self.probe_filter is not None:
            try:
                probe_result = await self.probe_filter.apply(enriched)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "probe_crashed",
                    mint=enriched.token_mint,
                    error=str(e),
                )
                probe_result = FilterResult(
                    filter_name=self.probe_filter.name,
                    passed=True,
                    hard_fail_reason=None,
                    sub_scores={},
                    evidence={"errors": [f"probe_crashed: {e}"]},
                )
            results.append(probe_result)

        fc = FilteredCandidate(
            enriched=enriched,
            filter_results=results,
            gate_passed=gate_passed,
            hard_fail_reason=hard_fail_reason,
        )

        await self._persist(fc)
        await self.filtered_bus.put(fc)
        logger.info(
            "candidate_filtered",
            mint=enriched.token_mint,
            gate_passed=gate_passed,
            hard_fail_reason=hard_fail_reason,
            filter_count=len(results),
        )

    async def _persist(self, fc: FilteredCandidate) -> None:
        if self.db is None or self.db.conn is None:
            return
        try:
            for result in fc.filter_results:
                await self.db.conn.execute(
                    """
                    INSERT INTO filter_results
                    (token_mint, filter_name, passed, hard_fail_reason,
                     sub_scores_json, evidence_json, cluster_signal_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fc.enriched.token_mint,
                        result.filter_name,
                        1 if result.passed else 0,
                        result.hard_fail_reason,
                        json.dumps(result.sub_scores),
                        json.dumps(result.evidence, default=str),
                        None,  # cluster_signal_id wired in Plan 2c if needed
                    ),
                )
            await self.db.conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "filter_results_persist_failed",
                mint=fc.enriched.token_mint,
                error=str(e),
            )
```

- [ ] **Step 4: Run tests — expect 5 passed**

```bash
python -m pytest tests/unit/test_filter_pipeline.py -v
```

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 117 passed (112 prior + 5 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/filters/pipeline.py meme-trading/runner/tests/unit/test_filter_pipeline.py
git commit -m "runner: FilterPipeline orchestrator with per-candidate tasks and DB persistence"
git push
```

---

## Task 9: Wire filter pipeline into main.py + integration test

**Why:** Final wiring. Replaces the `_drain_enriched` sink with the FilterPipeline, which consumes EnrichedTokens and emits FilteredCandidates. Adds a new `_drain_filtered` sink. Adds an integration test running the full pipeline.

**Files:**
- Modify: `main.py`
- Create: `tests/integration/test_enrichment_to_filters.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_enrichment_to_filters.py`:

```python
"""End-to-end: BuyEvent → cluster → enrichment → filters → FilteredCandidate."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.enrichment.enricher import Enricher
from runner.filters.base import BaseFilter, FilteredCandidate, FilterResult
from runner.filters.pipeline import FilterPipeline
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


class _PassFilter(BaseFilter):
    def __init__(self, name: str, sub_scores: dict[str, float]):
        self.name = name  # type: ignore[misc]
        self._sub_scores = sub_scores

    async def apply(self, enriched):
        return FilterResult(
            filter_name=self.name,
            passed=True,
            hard_fail_reason=None,
            sub_scores=self._sub_scores,
            evidence={},
        )


@pytest.mark.asyncio
async def test_full_pipeline_produces_filtered_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()

    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
    )

    metadata = AsyncMock()
    metadata.fetch.return_value = {
        "symbol": "E2E",
        "decimals": 6,
        "supply": 1e9,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.00025,
        "liquidity_usd": 30000.0,
        "slippage_at_size_pct": {0.25: 1.0},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "Dep1"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=[
            _PassFilter("rug", {"rug_risk": 90}),
            _PassFilter("holder", {"holder_quality": 70}),
        ],
        probe_filter=_PassFilter("follow_through", {"follow_through": 100}),
        db=db,
    )

    det_task = asyncio.create_task(detector.run())
    enr_task = asyncio.create_task(enricher.run())
    pipe_task = asyncio.create_task(pipeline.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate([("s1", "A1"), ("s2", "A2"), ("s3", "B1")]):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="E2E_MINT",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    fc: FilteredCandidate = await asyncio.wait_for(filtered_bus.get(), timeout=3.0)
    assert fc.enriched.token_mint == "E2E_MINT"
    assert fc.enriched.symbol == "E2E"
    assert fc.gate_passed is True
    assert len(fc.filter_results) == 3
    assert fc.filter_results[0].filter_name == "rug"
    assert fc.filter_results[2].filter_name == "follow_through"

    for t in (det_task, enr_task, pipe_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    await db.close()
```

- [ ] **Step 2: Run integration test — expect PASS**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/integration/test_enrichment_to_filters.py -v
```

Expected: 1 passed.

- [ ] **Step 3: Modify main.py**

Use Read tool on `meme-trading/runner/main.py` to see current state.

Add imports near the existing runner imports:

```python
from runner.filters.entry_quality import EntryQualityFilter
from runner.filters.follow_through import FollowThroughProbe
from runner.filters.holder_filter import HolderFilter
from runner.filters.insider_filter import InsiderFilter
from runner.filters.pipeline import FilterPipeline
from runner.filters.rug_gate import RugGate
```

Inside `_main()`, after the `enricher = Enricher(...)` block (and before the `logger.info("wired", ...)` block), add:

```python
    rug_gate = RugGate(
        http,
        lp_locked_pct_min=weights.get("gates.lp_locked_pct_min", 85),
    )
    holder_filter = HolderFilter(
        http,
        rpc_url=settings.helius_rpc_url,
        top10_max_pct=weights.get("gates.top10_max_pct", 70),
    )
    insider_filter = InsiderFilter(http)
    entry_quality_filter = EntryQualityFilter()
    follow_through_probe = FollowThroughProbe(
        db=db,
        tier_cache=tier_cache,
        price_fetcher=price_fetcher,
        probe_minutes=weights.get("probe.follow_through_minutes", 5),
    )

    filtered_bus: asyncio.Queue = asyncio.Queue()
    filter_pipeline = FilterPipeline(
        enriched_bus=enriched_bus,
        filtered_bus=filtered_bus,
        sync_filters=[rug_gate, holder_filter, insider_filter, entry_quality_filter],
        probe_filter=follow_through_probe,
        db=db,
    )
```

Replace the `asyncio.gather` block:

```python
    try:
        results = await asyncio.gather(
            _supervise(monitor.run, "wallet_monitor", logger),
            _supervise(detector.run, "convergence_detector", logger),
            _supervise(enricher.run, "enricher", logger),
            _supervise(filter_pipeline.run, "filter_pipeline", logger),
            _supervise(lambda: _drain_filtered(filtered_bus, logger), "drain_filtered", logger),
            return_exceptions=True,
        )
        for name, result in zip(
            ["monitor", "detector", "enricher", "filter_pipeline", "drain_filtered"],
            results,
        ):
            if isinstance(result, Exception):
                logger.error("task_exited_with_exception", task=name, error=str(result))
    finally:
        await http.aclose()
        await db.close()
```

**Delete** the old `_drain_enriched` function and add a new one:

```python
async def _drain_filtered(filtered_bus: asyncio.Queue, logger) -> None:
    """Phase 5 sink: log every filtered candidate. Replaced by Scoring engine in Plan 2c."""
    while True:
        try:
            fc = await filtered_bus.get()
            logger.info(
                "filtered_candidate_drained",
                mint=fc.enriched.token_mint,
                symbol=fc.enriched.symbol,
                gate_passed=fc.gate_passed,
                hard_fail_reason=fc.hard_fail_reason,
                filter_count=len(fc.filter_results),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_filtered_iteration_error", error=str(e))
```

- [ ] **Step 4: Run full suite**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/ -v
```

Expected: 118 passed (117 + 1 integration). No regressions.

- [ ] **Step 5: Verify main.py imports**

```bash
cd /c/Users/rakai/Leverage/meme-trading
python -c "import sys; sys.path.insert(0, '.'); from runner.main import _main, _drain_filtered, _supervise; print('main ok')"
```

Expected: `main ok`.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/main.py meme-trading/runner/tests/integration/test_enrichment_to_filters.py
git commit -m "runner: wire FilterPipeline into main.py and add enrichment→filters integration test"
git push
```

---

## End-of-plan verification

- [ ] **Step 1: Full test run**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/ -v --tb=short
```

Expected: 118 passed total.

- [ ] **Step 2: Sanity-check filter package imports**

```bash
cd /c/Users/rakai/Leverage/meme-trading
python -c "import sys; sys.path.insert(0, '.'); import runner.filters.base, runner.filters.rug_gate, runner.filters.holder_filter, runner.filters.insider_filter, runner.filters.entry_quality, runner.filters.follow_through, runner.filters.pipeline; print('filters ok')"
```

- [ ] **Step 3: Push final state**

```bash
cd /c/Users/rakai/Leverage
git log --oneline -15
git status       # should be clean
```

---

## What's next (Plan 2c preview)

Plan 2b ends with: FilterPipeline producing FilteredCandidates on a queue, with filter_results persisted to DB. No scoring yet — `_drain_filtered` just logs them.

**Plan 2c** will cover:
- **FactorScorer:** reads FilteredCandidate.filter_results + cluster_signal, computes all 7 sub-scores (wallet_quality, cluster_quality, entry_quality, holder_quality, rug_risk, follow_through, narrative)
- **RunnerScorer:** weighted combine → Runner Score (0-100)
- **VerdictAssigner:** tier assignment (Ignore / Watch / Strong / Probable) using `weights.yaml` thresholds
- **Explainer:** per-candidate evidence payload for future Telegram alerts
- **Persistence:** writes to `runner_scores` table
- **Integration:** `ScoringPipeline` consumes `filtered_bus`, emits on `scored_bus`, `_drain_scored` logs verdict

End state after Plan 2c: scored verdicts with full explanation written to DB, ready for Plan 3 (executor + Telegram alerts + dashboard).
