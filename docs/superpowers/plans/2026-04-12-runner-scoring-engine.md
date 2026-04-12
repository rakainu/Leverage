# Runner Scoring Engine (Plan 2c) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the scoring engine pipeline stage that computes a weighted Runner Score (0-100), assigns a verdict, and persists explainable results for every filtered candidate.

**Architecture:** Single `ScoringEngine` class in `scoring/engine.py` consumes `FilteredCandidate` from `filtered_bus`, derives 7 dimension scores, combines them with configurable weights, assigns a verdict, persists to `runner_scores` table, and emits `ScoredCandidate` onto `scored_bus`. Replaces the temporary `_drain_filtered` sink in `main.py`.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, PyYAML, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-scoring-engine-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `meme-trading/runner/scoring/__init__.py` | Package marker |
| Create | `meme-trading/runner/scoring/models.py` | `ScoredCandidate` dataclass, `Verdict` type alias, `DIMENSION_KEYS` constant |
| Create | `meme-trading/runner/scoring/engine.py` | `ScoringEngine` class — all scoring logic |
| Modify | `meme-trading/runner/cluster/convergence.py` | Add `id: int \| None` to `ClusterSignal`, populate from `lastrowid` |
| Modify | `meme-trading/runner/enrichment/schemas.py` | Add `cluster_signal_id: int \| None` to `EnrichedToken` |
| Modify | `meme-trading/runner/enrichment/enricher.py` | Thread `cluster_signal.id` → `EnrichedToken.cluster_signal_id` |
| Modify | `meme-trading/runner/filters/base.py` | Add `hard_fail_filter_name: str \| None` to `FilteredCandidate` |
| Modify | `meme-trading/runner/filters/pipeline.py` | Set `hard_fail_filter_name` on gate failure; use `cluster_signal_id` |
| Modify | `meme-trading/runner/db/schema.sql` | Add `short_circuited` column to `runner_scores` |
| Modify | `meme-trading/runner/db/database.py` | Add migration for `short_circuited` on existing DBs |
| Modify | `meme-trading/runner/config/weights.yaml` | Add `scoring` section |
| Modify | `meme-trading/runner/main.py` | Replace `_drain_filtered` with `ScoringEngine` + `_drain_scored` |
| Create | `meme-trading/runner/tests/unit/test_scored_candidate.py` | Model tests |
| Create | `meme-trading/runner/tests/unit/test_scoring_engine.py` | Pure scoring tests |
| Create | `meme-trading/runner/tests/unit/test_scoring_persist.py` | DB persistence tests |
| Create | `meme-trading/runner/tests/integration/test_filters_to_scoring.py` | End-to-end flow test |

---

### Task 1: Thread `cluster_signal_id` through upstream pipeline

**Files:**
- Modify: `meme-trading/runner/cluster/convergence.py:23-35` (ClusterSignal dataclass)
- Modify: `meme-trading/runner/cluster/convergence.py:131-176` (signal persistence + emit)
- Modify: `meme-trading/runner/enrichment/schemas.py:9-48` (EnrichedToken)
- Modify: `meme-trading/runner/enrichment/enricher.py:73-94` (EnrichedToken construction)
- Modify: `meme-trading/runner/filters/base.py:25-38` (FilteredCandidate)
- Modify: `meme-trading/runner/filters/pipeline.py:50-112` (process_one)
- Test: `meme-trading/runner/tests/unit/test_convergence.py`
- Test: `meme-trading/runner/tests/unit/test_filter_pipeline.py`

- [ ] **Step 1: Add `id` field to `ClusterSignal`**

In `meme-trading/runner/cluster/convergence.py`, add `id` as the last field in the dataclass:

```python
@dataclass(frozen=True)
class ClusterSignal:
    """Emitted when enough A+B wallets converge on a token."""

    token_mint: str
    wallets: list[str]
    wallet_count: int
    tier_counts: dict[str, int]
    first_buy_time: datetime
    last_buy_time: datetime
    convergence_seconds: int
    mid_price_sol: float
    id: int | None = None
```

- [ ] **Step 2: Populate `id` from `lastrowid` after DB INSERT**

In `convergence.py`, in the `_process` method, after the INSERT+commit block, use `dataclasses.replace` to set the id before emitting. Add `from dataclasses import replace` to the imports. Replace the persistence block (lines ~141-176):

```python
        if self.db is not None and self.db.conn is not None:
            try:
                cursor = await self.db.conn.execute(
                    """
                    INSERT INTO cluster_signals
                    (token_mint, wallet_count, wallets_json, tier_counts_json,
                     first_buy_time, last_buy_time, convergence_seconds, mid_price_sol)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.token_mint,
                        signal.wallet_count,
                        json.dumps(signal.wallets),
                        json.dumps(signal.tier_counts),
                        signal.first_buy_time.isoformat(),
                        signal.last_buy_time.isoformat(),
                        signal.convergence_seconds,
                        signal.mid_price_sol,
                    ),
                )
                await self.db.conn.commit()
                signal = replace(signal, id=cursor.lastrowid)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cluster_signal_persist_failed",
                    mint=signal.token_mint,
                    error=str(e),
                )
```

- [ ] **Step 3: Add `cluster_signal_id` field to `EnrichedToken`**

In `meme-trading/runner/enrichment/schemas.py`, add after the `errors` field:

```python
    # Pipeline threading — links back to the originating cluster_signals row
    cluster_signal_id: int | None = None
```

- [ ] **Step 4: Thread `cluster_signal.id` in enricher**

In `meme-trading/runner/enrichment/enricher.py`, in the `EnrichedToken(...)` construction at line 73, add at the end before the closing paren:

```python
            cluster_signal_id=signal.id,
```

- [ ] **Step 5: Add `hard_fail_filter_name` to `FilteredCandidate`**

In `meme-trading/runner/filters/base.py`, update the `FilteredCandidate` dataclass:

```python
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
```

- [ ] **Step 6: Set `hard_fail_filter_name` and use `cluster_signal_id` in pipeline**

In `meme-trading/runner/filters/pipeline.py`, update `_process_one` — when a gate fails, capture `f.name`:

Replace the gate failure block inside the for loop:

```python
            if not result.passed:
                gate_passed = False
                hard_fail_reason = result.hard_fail_reason
                hard_fail_filter_name = f.name
                break
```

Add `hard_fail_filter_name: str | None = None` initialization at the start of `_process_one`:

```python
    async def _process_one(self, enriched: EnrichedToken) -> None:
        results: list[FilterResult] = []
        gate_passed = True
        hard_fail_reason: str | None = None
        hard_fail_filter_name: str | None = None
```

Update the `FilteredCandidate` construction:

```python
        fc = FilteredCandidate(
            enriched=enriched,
            filter_results=results,
            gate_passed=gate_passed,
            hard_fail_reason=hard_fail_reason,
            hard_fail_filter_name=hard_fail_filter_name,
        )
```

Update `_persist` to use the threaded `cluster_signal_id`:

```python
                        fc.enriched.cluster_signal_id,  # was: None
```

- [ ] **Step 7: Run existing tests to verify no regressions**

Run: `cd meme-trading && python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -30`

Expected: All 118 tests pass. The new optional fields default to `None` so no existing code breaks.

- [ ] **Step 8: Commit**

```bash
git add meme-trading/runner/cluster/convergence.py meme-trading/runner/enrichment/schemas.py meme-trading/runner/enrichment/enricher.py meme-trading/runner/filters/base.py meme-trading/runner/filters/pipeline.py
git commit -m "runner: thread cluster_signal_id and hard_fail_filter_name through pipeline"
```

---

### Task 2: Schema migration + weights.yaml additions

**Files:**
- Modify: `meme-trading/runner/db/schema.sql`
- Modify: `meme-trading/runner/db/database.py`
- Modify: `meme-trading/runner/config/weights.yaml`
- Test: `meme-trading/runner/tests/unit/test_database.py`

- [ ] **Step 1: Write the failing test for schema migration**

Create a test in `meme-trading/runner/tests/unit/test_database.py` (append to existing file):

```python
@pytest.mark.asyncio
async def test_migration_adds_short_circuited_column(tmp_path):
    """Existing runner_scores table without short_circuited gets the column on migration."""
    db_path = tmp_path / "migrate.db"

    # Create the old schema without short_circuited
    import aiosqlite
    conn = await aiosqlite.connect(db_path)
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("""
        CREATE TABLE runner_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint TEXT NOT NULL,
            cluster_signal_id INTEGER,
            runner_score REAL NOT NULL,
            verdict TEXT NOT NULL,
            sub_scores_json TEXT NOT NULL,
            explanation_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.commit()
    await conn.close()

    # Now open with Database which should run migration
    from runner.db.database import Database
    db = Database(db_path)
    await db.connect()

    # Verify the column exists by inserting a row that uses it
    await db.conn.execute(
        """INSERT INTO runner_scores
           (token_mint, runner_score, verdict, short_circuited, sub_scores_json, explanation_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("MINT1", 0.0, "ignore", 1, "{}", "{}"),
    )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT short_circuited FROM runner_scores WHERE token_mint = 'MINT1'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == 1

    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_database.py::test_migration_adds_short_circuited_column -v`

Expected: FAIL — migration doesn't exist yet.

- [ ] **Step 3: Update `schema.sql` with `short_circuited` column**

In `meme-trading/runner/db/schema.sql`, replace the `runner_scores` table definition:

```sql
-- Final Runner Score + Verdict — one row per candidate (populated by scoring engine in Plan 2c).
CREATE TABLE IF NOT EXISTS runner_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    cluster_signal_id INTEGER,
    runner_score REAL NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('ignore', 'watch', 'strong_candidate', 'probable_runner')),
    short_circuited INTEGER DEFAULT 0,
    sub_scores_json TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Add migration logic to `Database._ensure_schema`**

In `meme-trading/runner/db/database.py`, add a migration method and call it from `_ensure_schema`:

```python
    async def _ensure_schema(self) -> None:
        assert self.conn is not None
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self.conn.executescript(schema_sql)
        await self.conn.commit()
        await self._run_migrations()

    async def _run_migrations(self) -> None:
        """Apply schema migrations for columns added after initial table creation."""
        assert self.conn is not None
        # Migration 1: add short_circuited to runner_scores (Plan 2c)
        async with self.conn.execute("PRAGMA table_info(runner_scores)") as cur:
            columns = {row[1] async for row in cur}
        if "short_circuited" not in columns:
            await self.conn.execute(
                "ALTER TABLE runner_scores ADD COLUMN short_circuited INTEGER DEFAULT 0"
            )
            await self.conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_database.py::test_migration_adds_short_circuited_column -v`

Expected: PASS

- [ ] **Step 6: Add `scoring` section to `weights.yaml`**

Append to `meme-trading/runner/config/weights.yaml`:

```yaml

scoring:
  rug_insider_rug_weight: 0.70
  insider_cap_threshold: 25
  insider_cap_value: 35
  neutral_fallback: 50
  version: "v1"
  reload_interval_sec: 30
```

- [ ] **Step 7: Run all tests to verify no regressions**

Run: `cd meme-trading && python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -30`

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add meme-trading/runner/db/schema.sql meme-trading/runner/db/database.py meme-trading/runner/config/weights.yaml meme-trading/runner/tests/unit/test_database.py
git commit -m "runner: schema migration for short_circuited + scoring weights config"
```

---

### Task 3: `ScoredCandidate` model + `DIMENSION_KEYS`

**Files:**
- Create: `meme-trading/runner/scoring/__init__.py`
- Create: `meme-trading/runner/scoring/models.py`
- Create: `meme-trading/runner/tests/unit/test_scored_candidate.py`

- [ ] **Step 1: Write the failing test for ScoredCandidate**

Create `meme-trading/runner/tests/unit/test_scored_candidate.py`:

```python
"""ScoredCandidate model tests."""
from datetime import datetime, timedelta, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS, ScoredCandidate, Verdict


def _make_scored() -> ScoredCandidate:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint="MINT1",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14),
        convergence_seconds=840,
        mid_price_sol=0.0005,
    )
    enriched = EnrichedToken(
        token_mint="MINT1",
        cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
    )
    fc = FilteredCandidate(
        enriched=enriched,
        filter_results=[],
        gate_passed=True,
        hard_fail_reason=None,
    )
    return ScoredCandidate(
        filtered=fc,
        runner_score=65.3,
        verdict="strong_candidate",
        dimension_scores={k: 50.0 for k in DIMENSION_KEYS},
        explanation={"short_circuited": False},
        scored_at=base + timedelta(minutes=16),
    )


def test_scored_candidate_is_frozen():
    sc = _make_scored()
    assert sc.runner_score == 65.3
    assert sc.verdict == "strong_candidate"
    # frozen — cannot reassign
    try:
        sc.runner_score = 99.0  # type: ignore[misc]
        assert False, "should have raised"
    except AttributeError:
        pass


def test_dimension_keys_has_seven_entries():
    assert len(DIMENSION_KEYS) == 7
    assert "wallet_quality" in DIMENSION_KEYS
    assert "cluster_quality" in DIMENSION_KEYS
    assert "entry_quality" in DIMENSION_KEYS
    assert "holder_quality" in DIMENSION_KEYS
    assert "rug_risk" in DIMENSION_KEYS
    assert "follow_through" in DIMENSION_KEYS
    assert "narrative" in DIMENSION_KEYS


def test_verdict_type_accepts_valid_values():
    # Verdict is a Literal type — we just check the constant list
    valid: list[Verdict] = ["ignore", "watch", "strong_candidate", "probable_runner"]
    assert len(valid) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scored_candidate.py -v`

Expected: FAIL — `runner.scoring.models` does not exist.

- [ ] **Step 3: Create scoring package and models**

Create `meme-trading/runner/scoring/__init__.py`:

```python
```

Create `meme-trading/runner/scoring/models.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scored_candidate.py -v`

Expected: PASS — 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/scoring/__init__.py meme-trading/runner/scoring/models.py meme-trading/runner/tests/unit/test_scored_candidate.py
git commit -m "runner: ScoredCandidate model and DIMENSION_KEYS constant"
```

---

### Task 4: `ScoringEngine` — verdict + combine + weight validation

**Files:**
- Create: `meme-trading/runner/scoring/engine.py`
- Create: `meme-trading/runner/tests/unit/test_scoring_engine.py`

- [ ] **Step 1: Write failing tests for verdict assignment and score combination**

Create `meme-trading/runner/tests/unit/test_scoring_engine.py`:

```python
"""Pure scoring engine unit tests — no DB, no queues."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS


# ──── helpers ────────────────────────────────────────────────────────

def _weights_file(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Write a weights.yaml with default scoring config; apply overrides."""
    data = {
        "weights": {
            "wallet_quality": 0.20,
            "cluster_quality": 0.15,
            "entry_quality": 0.15,
            "holder_quality": 0.15,
            "rug_risk": 0.15,
            "follow_through": 0.15,
            "narrative": 0.05,
        },
        "verdict_thresholds": {
            "watch": 40,
            "strong_candidate": 60,
            "probable_runner": 78,
        },
        "scoring": {
            "rug_insider_rug_weight": 0.70,
            "insider_cap_threshold": 25,
            "insider_cap_value": 35,
            "neutral_fallback": 50,
            "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {
            "speed_bonus_sweet_spot_min": 10,
            "speed_bonus_sweet_spot_max": 20,
        },
    }
    if overrides:
        for dotted_key, val in overrides.items():
            parts = dotted_key.split(".")
            node = data
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = val
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _cluster_signal(
    wallets=("A1", "A2", "B1"),
    convergence_seconds=840,
    mid_price=0.0005,
) -> ClusterSignal:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT1",
        wallets=list(wallets),
        wallet_count=len(wallets),
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(seconds=convergence_seconds),
        convergence_seconds=convergence_seconds,
        mid_price_sol=mid_price,
        id=42,
    )


def _enriched(signal: ClusterSignal | None = None) -> EnrichedToken:
    sig = signal or _cluster_signal()
    return EnrichedToken(
        token_mint=sig.token_mint,
        cluster_signal=sig,
        enriched_at=datetime(2026, 4, 12, 10, 15, tzinfo=timezone.utc),
        price_sol=0.0006,
        cluster_signal_id=sig.id,
    )


def _all_pass_results() -> list[FilterResult]:
    """Full set of filter results with known sub-scores."""
    return [
        FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]


def _filtered(
    gate_passed=True,
    hard_fail_reason=None,
    hard_fail_filter_name=None,
    results=None,
    signal=None,
) -> FilteredCandidate:
    enriched = _enriched(signal)
    return FilteredCandidate(
        enriched=enriched,
        filter_results=results if results is not None else _all_pass_results(),
        gate_passed=gate_passed,
        hard_fail_reason=hard_fail_reason,
        hard_fail_filter_name=hard_fail_filter_name,
    )


def _tier_cache_stub(tier_map: dict[str, Tier] | None = None) -> WalletTierCache:
    """Build a WalletTierCache stub without a real DB."""
    class _Stub(WalletTierCache):
        def __init__(self, mapping):
            self._map = mapping or {}
    return _Stub(tier_map or {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})


def _engine(tmp_path, tier_map=None, weight_overrides=None):
    from runner.scoring.engine import ScoringEngine
    weights = WeightsLoader(_weights_file(tmp_path, weight_overrides))
    tier_cache = _tier_cache_stub(tier_map)
    return ScoringEngine(
        filtered_bus=asyncio.Queue(),
        scored_bus=asyncio.Queue(),
        weights=weights,
        tier_cache=tier_cache,
    )


# ──── verdict assignment ─────────────────────────────────────────────

def test_verdict_ignore(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(0.0) == "ignore"
    assert eng._assign_verdict(39.9) == "ignore"


def test_verdict_watch(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(40.0) == "watch"
    assert eng._assign_verdict(59.9) == "watch"


def test_verdict_strong_candidate(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(60.0) == "strong_candidate"
    assert eng._assign_verdict(77.9) == "strong_candidate"


def test_verdict_probable_runner(tmp_path):
    eng = _engine(tmp_path)
    assert eng._assign_verdict(78.0) == "probable_runner"
    assert eng._assign_verdict(100.0) == "probable_runner"


# ──── score combination ──────────────────────────────────────────────

def test_combine_scores_weighted_sum(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: 50.0 for k in DIMENSION_KEYS}  # all 50 → 50 * 1.0 = 50
    assert eng._combine_scores(dims) == pytest.approx(50.0)


def test_combine_scores_clamped_to_100(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: 200.0 for k in DIMENSION_KEYS}
    assert eng._combine_scores(dims) == 100.0


def test_combine_scores_clamped_to_0(tmp_path):
    eng = _engine(tmp_path)
    dims = {k: -50.0 for k in DIMENSION_KEYS}
    assert eng._combine_scores(dims) == 0.0


# ──── weight validation ──────────────────────────────────────────────

def test_validate_weights_passes_for_valid_weights(tmp_path):
    eng = _engine(tmp_path)
    # Should not raise
    eng._validate_weights()


def test_validate_weights_warns_on_bad_sum(tmp_path):
    eng = _engine(tmp_path, weight_overrides={"weights.narrative": 0.50})
    # Total = 0.20+0.15*5+0.50 = 1.45, should raise ValueError
    with pytest.raises(ValueError, match="sum"):
        eng._validate_weights()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scoring_engine.py -v`

Expected: FAIL — `runner.scoring.engine` does not exist.

- [ ] **Step 3: Create `ScoringEngine` with verdict, combine, and weight validation**

Create `meme-trading/runner/scoring/engine.py`:

```python
"""ScoringEngine — single-class scoring pipeline stage.

Consumes FilteredCandidate from filtered_bus, computes a weighted
Runner Score (0-100), assigns a verdict, persists to runner_scores,
and emits ScoredCandidate onto scored_bus.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from runner.cluster.wallet_tier import WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.models import DIMENSION_KEYS, ScoredCandidate, Verdict
from runner.utils.logging import get_logger

logger = get_logger("runner.scoring.engine")


class ScoringEngine:
    """Scores filtered candidates and assigns verdicts.

    Public interface:
        run()  — long-lived async bus consumer (operational glue)
        score(fc) — pure scoring logic (no I/O, no side effects)
    """

    def __init__(
        self,
        filtered_bus: asyncio.Queue,
        scored_bus: asyncio.Queue,
        weights: WeightsLoader,
        tier_cache: WalletTierCache,
        db: Database | None = None,
    ):
        self.filtered_bus = filtered_bus
        self.scored_bus = scored_bus
        self.weights = weights
        self.tier_cache = tier_cache
        self.db = db
        self._reload_interval_sec: float = float(
            weights.get("scoring.reload_interval_sec", 30)
        )
        self._last_reload_check: float = time.monotonic()
        # Validate on startup — fail fast if initial weights are invalid.
        self._validate_weights()

    # ── public ─────────────────────────────────────────────────────

    def score(self, fc: FilteredCandidate) -> ScoredCandidate:
        """Pure scoring — no I/O, no reload checks. Primary test target."""
        now = datetime.now(timezone.utc)

        if not fc.gate_passed:
            return self._short_circuit(fc, now)

        dimensions = self._derive_dimensions(fc)
        runner_score = self._combine_scores(dimensions)
        verdict = self._assign_verdict(runner_score)
        explanation = self._build_explanation(fc, dimensions, runner_score, verdict)

        return ScoredCandidate(
            filtered=fc,
            runner_score=runner_score,
            verdict=verdict,
            dimension_scores=dimensions,
            explanation=explanation,
            scored_at=now,
        )

    # ── verdict + combine ──────────────────────────────────────────

    def _assign_verdict(self, score: float) -> Verdict:
        """Map score to verdict using threshold bands from weights.yaml."""
        pr = float(self.weights.get("verdict_thresholds.probable_runner", 78))
        sc = float(self.weights.get("verdict_thresholds.strong_candidate", 60))
        w = float(self.weights.get("verdict_thresholds.watch", 40))
        if score >= pr:
            return "probable_runner"
        if score >= sc:
            return "strong_candidate"
        if score >= w:
            return "watch"
        return "ignore"

    def _combine_scores(self, dimensions: dict[str, float]) -> float:
        """Weighted sum of dimension scores. Returns 0-100."""
        total = 0.0
        for key, dim_score in dimensions.items():
            weight = float(self.weights.get(f"weights.{key}", 0.0))
            total += weight * dim_score
        return max(0.0, min(100.0, total))

    def _validate_weights(self) -> None:
        """Check that dimension weights sum to ~1.0. Raises ValueError if not."""
        total = sum(
            float(self.weights.get(f"weights.{k}", 0.0)) for k in DIMENSION_KEYS
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Scoring dimension weights sum to {total:.4f}, expected ~1.0 "
                f"(tolerance 0.01). Check weights.yaml [weights] section."
            )

    # ── dimension derivation ───────────────────────────────────────

    def _derive_dimensions(self, fc: FilteredCandidate) -> dict[str, float]:
        """Build all 7 dimension scores from filter results + cluster signal."""
        missing: list[str] = []

        entry_quality = self._lookup_sub_score(fc, "entry_quality", "entry_quality", missing)
        holder_quality = self._lookup_sub_score(fc, "holder_filter", "holder_quality", missing)
        follow_through = self._lookup_sub_score(fc, "follow_through", "follow_through", missing)

        raw_rug = self._lookup_sub_score(fc, "rug_gate", "rug_risk", missing)
        raw_insider = self._lookup_sub_score(fc, "insider_filter", "insider_risk", missing)
        rug_risk = self._combine_rug_risk(raw_rug, raw_insider)

        wallet_quality = self._derive_wallet_quality(fc, missing)
        cluster_quality = self._derive_cluster_quality(fc)
        narrative = 50.0  # neutral placeholder

        return {
            "wallet_quality": wallet_quality,
            "cluster_quality": cluster_quality,
            "entry_quality": entry_quality,
            "holder_quality": holder_quality,
            "rug_risk": rug_risk,
            "follow_through": follow_through,
            "narrative": narrative,
        }

    def _lookup_sub_score(
        self,
        fc: FilteredCandidate,
        filter_name: str,
        score_key: str,
        missing: list[str],
    ) -> float:
        """Look up a sub-score from filter results. Returns neutral fallback if missing."""
        fallback = float(self.weights.get("scoring.neutral_fallback", 50))
        for result in fc.filter_results:
            if result.filter_name == filter_name:
                val = result.sub_scores.get(score_key)
                if val is not None:
                    return float(val)
                missing.append(score_key)
                return fallback
        missing.append(score_key)
        return fallback

    def _combine_rug_risk(self, raw_rug: float, raw_insider: float) -> float:
        """Weighted average of rug + insider with severe insider cap."""
        rug_w = float(self.weights.get("scoring.rug_insider_rug_weight", 0.70))
        insider_w = 1.0 - rug_w
        combined = rug_w * raw_rug + insider_w * raw_insider

        cap_threshold = float(self.weights.get("scoring.insider_cap_threshold", 25))
        cap_value = float(self.weights.get("scoring.insider_cap_value", 35))
        if raw_insider < cap_threshold:
            combined = min(combined, cap_value)

        return max(0.0, min(100.0, combined))

    def _derive_wallet_quality(
        self, fc: FilteredCandidate, missing: list[str]
    ) -> float:
        """Mean tier points of wallets in the cluster."""
        wallets = fc.enriched.cluster_signal.wallets
        if not wallets:
            missing.append("wallet_quality")
            fallback = float(self.weights.get("scoring.neutral_fallback", 50))
            return fallback
        points = [self.tier_cache.tier_of(w).points for w in wallets]
        return max(0.0, min(100.0, mean(points)))

    def _derive_cluster_quality(self, fc: FilteredCandidate) -> float:
        """Heuristic v1 cluster quality from wallet count + convergence speed."""
        signal = fc.enriched.cluster_signal
        base = 50.0
        wallet_bonus = min((signal.wallet_count - 3) * 10, 30)

        conv_min = signal.convergence_seconds / 60.0
        sweet_min = float(self.weights.get("cluster.speed_bonus_sweet_spot_min", 10))
        sweet_max = float(self.weights.get("cluster.speed_bonus_sweet_spot_max", 20))

        if sweet_min <= conv_min <= sweet_max:
            speed_bonus = 20.0
        elif 5.0 <= conv_min < sweet_min:
            speed_bonus = 10.0
        elif sweet_max < conv_min <= 30.0:
            speed_bonus = 10.0
        elif conv_min < 5.0:
            speed_bonus = -20.0
        else:
            speed_bonus = 0.0

        return max(0.0, min(100.0, base + wallet_bonus + speed_bonus))

    # ── short circuit ──────────────────────────────────────────────

    def _short_circuit(
        self, fc: FilteredCandidate, now: datetime
    ) -> ScoredCandidate:
        """Score a gate-failed candidate: score=0, verdict=ignore."""
        dimensions = {k: 0.0 for k in DIMENSION_KEYS}
        explanation = self._build_explanation(fc, dimensions, 0.0, "ignore")
        explanation["short_circuited"] = True
        explanation["failed_gate"] = fc.hard_fail_filter_name
        explanation["failed_reason"] = fc.hard_fail_reason
        return ScoredCandidate(
            filtered=fc,
            runner_score=0.0,
            verdict="ignore",
            dimension_scores=dimensions,
            explanation=explanation,
            scored_at=now,
        )

    # ── explanation ────────────────────────────────────────────────

    def _build_explanation(
        self,
        fc: FilteredCandidate,
        dimensions: dict[str, float],
        score: float,
        verdict: Verdict,
    ) -> dict[str, Any]:
        """Build full explanation dict for DB persistence + alerts."""
        # Detect missing/degraded sub-scores
        missing = self._find_missing_subscores(fc) if fc.gate_passed else []

        # Compute weights hash from scoring config section
        scoring_cfg = self.weights.get("scoring", {})
        weights_cfg = self.weights.get("weights", {})
        hash_input = json.dumps({"scoring": scoring_cfg, "weights": weights_cfg}, sort_keys=True)
        weights_hash = hashlib.md5(hash_input.encode()).hexdigest()[:6]

        dim_detail: dict[str, dict[str, Any]] = {}
        for key in DIMENSION_KEYS:
            w = float(self.weights.get(f"weights.{key}", 0.0))
            s = dimensions[key]
            detail: dict[str, Any] = {}

            if key == "wallet_quality" and fc.gate_passed:
                wallets = fc.enriched.cluster_signal.wallets
                tiers = [self.tier_cache.tier_of(w_addr).label for w_addr in wallets]
                pts = [self.tier_cache.tier_of(w_addr).points for w_addr in wallets]
                detail = {"wallets": len(wallets), "tiers": tiers, "points": pts}
            elif key == "cluster_quality" and fc.gate_passed:
                sig = fc.enriched.cluster_signal
                conv_min = sig.convergence_seconds / 60.0
                detail = {
                    "wallet_count": sig.wallet_count,
                    "convergence_minutes": conv_min,
                }
            elif key == "rug_risk" and fc.gate_passed:
                raw_rug = self._lookup_sub_score_raw(fc, "rug_gate", "rug_risk")
                raw_ins = self._lookup_sub_score_raw(fc, "insider_filter", "insider_risk")
                cap_threshold = float(self.weights.get("scoring.insider_cap_threshold", 25))
                detail = {
                    "raw_rug": raw_rug,
                    "raw_insider": raw_ins,
                    "rug_weight": float(self.weights.get("scoring.rug_insider_rug_weight", 0.70)),
                    "insider_capped": raw_ins is not None and raw_ins < cap_threshold,
                }
            elif key == "narrative":
                detail = {"placeholder": True}

            dim_detail[key] = {
                "score": s,
                "weight": w,
                "weighted": round(w * s, 4),
                "detail": detail,
            }

        return {
            "scoring_version": self.weights.get("scoring.version", "v1"),
            "weights_mtime": self.weights.last_loaded_mtime,
            "weights_hash": weights_hash,
            "short_circuited": not fc.gate_passed,
            "data_degraded": len(missing) > 0,
            "missing_subscores": missing,
            "failed_gate": fc.hard_fail_filter_name if not fc.gate_passed else None,
            "failed_reason": fc.hard_fail_reason if not fc.gate_passed else None,
            "dimensions": dim_detail,
            "verdict_thresholds": {
                "watch": float(self.weights.get("verdict_thresholds.watch", 40)),
                "strong_candidate": float(self.weights.get("verdict_thresholds.strong_candidate", 60)),
                "probable_runner": float(self.weights.get("verdict_thresholds.probable_runner", 78)),
            },
        }

    def _find_missing_subscores(self, fc: FilteredCandidate) -> list[str]:
        """Identify sub-scores that fell back to neutral due to missing data."""
        expected = {
            "rug_gate": "rug_risk",
            "holder_filter": "holder_quality",
            "insider_filter": "insider_risk",
            "entry_quality": "entry_quality",
            "follow_through": "follow_through",
        }
        result_map = {r.filter_name: r for r in fc.filter_results}
        missing = []
        for filter_name, score_key in expected.items():
            result = result_map.get(filter_name)
            if result is None or result.sub_scores.get(score_key) is None:
                missing.append(score_key)
        return missing

    def _lookup_sub_score_raw(
        self, fc: FilteredCandidate, filter_name: str, score_key: str
    ) -> float | None:
        """Raw lookup without fallback — for explanation detail only."""
        for result in fc.filter_results:
            if result.filter_name == filter_name:
                return result.sub_scores.get(score_key)
        return None

    # ── async run loop ─────────────────────────────────────────────

    async def run(self) -> None:
        """Long-lived consumer: read filtered_bus, score, persist, emit."""
        logger.info("scoring_engine_start")
        while True:
            fc: FilteredCandidate = await self.filtered_bus.get()

            # TTL-guarded weight reload
            now_mono = time.monotonic()
            if now_mono - self._last_reload_check >= self._reload_interval_sec:
                self._last_reload_check = now_mono
                if self.weights.check_and_reload():
                    try:
                        self._validate_weights()
                        logger.info("weights_reloaded")
                    except ValueError as e:
                        logger.warning(
                            "weights_reload_invalid_reverting",
                            error=str(e),
                        )

            scored = self.score(fc)
            await self._persist(scored)
            await self.scored_bus.put(scored)

            logger.info(
                "candidate_scored",
                mint=scored.filtered.enriched.token_mint,
                score=round(scored.runner_score, 2),
                verdict=scored.verdict,
                short_circuited=scored.explanation.get("short_circuited", False),
            )

    async def _persist(self, sc: ScoredCandidate) -> None:
        """Insert scored candidate into runner_scores table."""
        if self.db is None or self.db.conn is None:
            return

        # Build sub_scores_json: 7 dimensions + 2 raw components
        sub_scores = dict(sc.dimension_scores)
        raw_rug = self._lookup_sub_score_raw(
            sc.filtered, "rug_gate", "rug_risk"
        )
        raw_insider = self._lookup_sub_score_raw(
            sc.filtered, "insider_filter", "insider_risk"
        )
        sub_scores["raw_rug_risk"] = raw_rug if raw_rug is not None else 0.0
        sub_scores["raw_insider_risk"] = raw_insider if raw_insider is not None else 0.0

        try:
            await self.db.conn.execute(
                """
                INSERT INTO runner_scores
                (token_mint, cluster_signal_id, runner_score, verdict,
                 short_circuited, sub_scores_json, explanation_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sc.filtered.enriched.token_mint,
                    sc.filtered.enriched.cluster_signal_id,
                    sc.runner_score,
                    sc.verdict,
                    1 if sc.explanation.get("short_circuited") else 0,
                    json.dumps(sub_scores),
                    json.dumps(sc.explanation, default=str),
                ),
            )
            await self.db.conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "runner_scores_persist_failed",
                mint=sc.filtered.enriched.token_mint,
                error=str(e),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scoring_engine.py -v`

Expected: All 7 tests pass (4 verdict + 3 combine/validate).

- [ ] **Step 5: Commit**

```bash
git add meme-trading/runner/scoring/engine.py meme-trading/runner/tests/unit/test_scoring_engine.py
git commit -m "runner: ScoringEngine with verdict, combine, and weight validation"
```

---

### Task 5: Dimension derivation tests

**Files:**
- Modify: `meme-trading/runner/tests/unit/test_scoring_engine.py`

- [ ] **Step 1: Add dimension derivation tests**

Append to `meme-trading/runner/tests/unit/test_scoring_engine.py`:

```python
# ──── dimension derivation ───────────────────────────────────────

def test_direct_filter_lookup(tmp_path):
    """entry_quality, holder_quality, follow_through read from filter results."""
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["entry_quality"] == 75.0
    assert dims["holder_quality"] == 60.0
    assert dims["follow_through"] == 60.0


def test_missing_sub_score_uses_neutral_fallback(tmp_path):
    """Missing filter result falls back to neutral_fallback (50)."""
    eng = _engine(tmp_path)
    # Only rug_gate result — everything else is missing
    results = [FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {})]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    assert dims["entry_quality"] == 50.0  # neutral fallback
    assert dims["holder_quality"] == 50.0
    assert dims["follow_through"] == 50.0


def test_rug_risk_weighted_average(tmp_path):
    """rug_risk = 0.7 * rug + 0.3 * insider."""
    eng = _engine(tmp_path)
    fc = _filtered()  # rug=80, insider=70
    dims = eng._derive_dimensions(fc)
    expected = 0.70 * 80.0 + 0.30 * 70.0  # 56 + 21 = 77
    assert dims["rug_risk"] == pytest.approx(expected)


def test_rug_risk_insider_cap(tmp_path):
    """Insider < 25 caps combined rug_risk at 35."""
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 20.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    # Without cap: 0.7*90 + 0.3*20 = 63+6 = 69. But insider=20 < 25 → cap at 35
    assert dims["rug_risk"] == 35.0


def test_rug_risk_no_cap_when_insider_above_threshold(tmp_path):
    """Insider >= 25 does not trigger cap."""
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 30.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    dims = eng._derive_dimensions(fc)
    expected = 0.70 * 90.0 + 0.30 * 30.0  # 63 + 9 = 72
    assert dims["rug_risk"] == pytest.approx(expected)


def test_wallet_quality_mixed_tiers(tmp_path):
    """A(100) + A(100) + B(60) → mean = 86.67."""
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    expected = mean([100, 100, 60])  # 86.67
    assert dims["wallet_quality"] == pytest.approx(expected, abs=0.1)


def test_wallet_quality_all_a_tier(tmp_path):
    eng = _engine(tmp_path, tier_map={"A1": Tier.A, "A2": Tier.A, "B1": Tier.A})
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["wallet_quality"] == 100.0


def test_wallet_quality_all_u_tier(tmp_path):
    eng = _engine(tmp_path, tier_map={})  # empty map → all default to U
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["wallet_quality"] == 40.0


def test_wallet_quality_unknown_wallets(tmp_path):
    """Wallets not in tier cache default to U(40). No crash."""
    eng = _engine(tmp_path, tier_map={"A1": Tier.A})  # A2, B1 unknown
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    expected = mean([100, 40, 40])  # 60
    assert dims["wallet_quality"] == pytest.approx(expected, abs=0.1)


def test_cluster_quality_sweet_spot(tmp_path):
    """14 min convergence (in 10-20 sweet spot) + 3 wallets → 50+0+20=70."""
    eng = _engine(tmp_path)
    fc = _filtered()  # 840s = 14 min, 3 wallets
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 70.0


def test_cluster_quality_fast_convergence_penalty(tmp_path):
    """< 5 min convergence gets -20 penalty → 50+0-20=30."""
    eng = _engine(tmp_path)
    sig = _cluster_signal(convergence_seconds=180)  # 3 min
    fc = _filtered(signal=sig)
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 30.0


def test_cluster_quality_extra_wallets(tmp_path):
    """6 wallets → +30 bonus (capped). 14 min → +20. Total = 50+30+20=100."""
    eng = _engine(tmp_path)
    sig = _cluster_signal(wallets=["A1", "A2", "B1", "B2", "B3", "B4"])
    fc = _filtered(signal=sig)
    dims = eng._derive_dimensions(fc)
    assert dims["cluster_quality"] == 100.0


def test_narrative_is_50(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered()
    dims = eng._derive_dimensions(fc)
    assert dims["narrative"] == 50.0
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scoring_engine.py -v`

Expected: All tests pass (7 previous + 13 new = 20 total).

- [ ] **Step 3: Commit**

```bash
git add meme-trading/runner/tests/unit/test_scoring_engine.py
git commit -m "runner: dimension derivation tests for scoring engine"
```

---

### Task 6: Short-circuit + explanation + full score tests

**Files:**
- Modify: `meme-trading/runner/tests/unit/test_scoring_engine.py`

- [ ] **Step 1: Add short-circuit and explanation tests**

Append to `meme-trading/runner/tests/unit/test_scoring_engine.py`:

```python
# ──── short-circuit ──────────────────────────────────────────────

def test_short_circuit_produces_ignore(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(
        gate_passed=False,
        hard_fail_reason="mint authority not revoked",
        hard_fail_filter_name="rug_gate",
        results=[FilterResult("rug_gate", False, "mint authority not revoked", {"rug_risk": 0}, {})],
    )
    sc = eng.score(fc)
    assert sc.runner_score == 0.0
    assert sc.verdict == "ignore"
    assert all(v == 0.0 for v in sc.dimension_scores.values())
    assert len(sc.dimension_scores) == 7
    assert sc.explanation["short_circuited"] is True
    assert sc.explanation["failed_gate"] == "rug_gate"
    assert sc.explanation["failed_reason"] == "mint authority not revoked"


def test_short_circuit_all_dimension_keys_present(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(gate_passed=False, hard_fail_reason="bad", hard_fail_filter_name="rug_gate",
                   results=[FilterResult("rug_gate", False, "bad", {}, {})])
    sc = eng.score(fc)
    for key in DIMENSION_KEYS:
        assert key in sc.dimension_scores
        assert key in sc.explanation["dimensions"]


# ──── explanation structure ──────────────────────────────────────

def test_explanation_has_version_markers(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    assert sc.explanation["scoring_version"] == "v1"
    assert "weights_mtime" in sc.explanation
    assert "weights_hash" in sc.explanation
    assert len(sc.explanation["weights_hash"]) == 6


def test_explanation_dimensions_have_required_keys(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    for key in DIMENSION_KEYS:
        dim = sc.explanation["dimensions"][key]
        assert "score" in dim
        assert "weight" in dim
        assert "weighted" in dim
        assert "detail" in dim


def test_explanation_verdict_thresholds_present(tmp_path):
    eng = _engine(tmp_path)
    sc = eng.score(_filtered())
    vt = sc.explanation["verdict_thresholds"]
    assert vt == {"watch": 40, "strong_candidate": 60, "probable_runner": 78}


def test_explanation_data_degraded_on_missing_scores(tmp_path):
    eng = _engine(tmp_path)
    fc = _filtered(results=[])  # no filter results at all
    sc = eng.score(fc)
    assert sc.explanation["data_degraded"] is True
    assert len(sc.explanation["missing_subscores"]) > 0


def test_explanation_rug_detail_insider_capped(tmp_path):
    eng = _engine(tmp_path)
    results = [
        FilterResult("rug_gate", True, None, {"rug_risk": 90.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 20.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]
    fc = _filtered(results=results)
    sc = eng.score(fc)
    rug_detail = sc.explanation["dimensions"]["rug_risk"]["detail"]
    assert rug_detail["insider_capped"] is True
    assert rug_detail["raw_rug"] == 90.0
    assert rug_detail["raw_insider"] == 20.0


# ──── full end-to-end score ──────────────────────────────────────

def test_full_score_known_inputs(tmp_path):
    """Verify the complete pipeline with known inputs produces expected result."""
    eng = _engine(tmp_path)
    fc = _filtered()  # rug=80, insider=70, holder=60, entry=75, follow=60
    sc = eng.score(fc)

    # wallet_quality = mean(100, 100, 60) = 86.67
    # cluster_quality = 50 + 0 + 20 = 70 (14 min sweet spot, 3 wallets)
    # entry_quality = 75
    # holder_quality = 60
    # rug_risk = 0.7*80 + 0.3*70 = 77
    # follow_through = 60
    # narrative = 50
    # score = 0.20*86.67 + 0.15*70 + 0.15*75 + 0.15*60 + 0.15*77 + 0.15*60 + 0.05*50
    #        = 17.33 + 10.5 + 11.25 + 9.0 + 11.55 + 9.0 + 2.5 = 71.13
    assert sc.runner_score == pytest.approx(71.13, abs=0.5)
    assert sc.verdict == "strong_candidate"  # 60 ≤ 71.13 < 78
    assert len(sc.dimension_scores) == 7
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scoring_engine.py -v`

Expected: All tests pass (20 previous + 8 new = 28 total).

- [ ] **Step 3: Commit**

```bash
git add meme-trading/runner/tests/unit/test_scoring_engine.py
git commit -m "runner: short-circuit, explanation, and full-score scoring tests"
```

---

### Task 7: Persistence tests

**Files:**
- Create: `meme-trading/runner/tests/unit/test_scoring_persist.py`

- [ ] **Step 1: Write persistence tests**

Create `meme-trading/runner/tests/unit/test_scoring_persist.py`:

```python
"""Scoring engine persistence tests — real DB, no queues."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import FilteredCandidate, FilterResult
from runner.scoring.engine import ScoringEngine
from runner.scoring.models import DIMENSION_KEYS


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _tier_cache_stub(tier_map=None):
    class _Stub(WalletTierCache):
        def __init__(self, mapping):
            self._map = mapping or {}
    return _Stub(tier_map or {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})


def _signal(signal_id=42):
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT1", wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=signal_id,
    )


def _enriched(sig=None):
    s = sig or _signal()
    return EnrichedToken(
        token_mint=s.token_mint, cluster_signal=s,
        enriched_at=datetime(2026, 4, 12, 10, 15, tzinfo=timezone.utc),
        price_sol=0.0006, cluster_signal_id=s.id,
    )


def _all_pass_results():
    return [
        FilterResult("rug_gate", True, None, {"rug_risk": 80.0}, {}),
        FilterResult("holder_filter", True, None, {"holder_quality": 60.0}, {}),
        FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {}),
        FilterResult("entry_quality", True, None, {"entry_quality": 75.0}, {}),
        FilterResult("follow_through", True, None, {"follow_through": 60.0}, {}),
    ]


@pytest.mark.asyncio
async def test_persist_normal_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    fc = FilteredCandidate(
        enriched=_enriched(), filter_results=_all_pass_results(),
        gate_passed=True, hard_fail_reason=None,
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT * FROM runner_scores WHERE token_mint='MINT1'") as cur:
        row = await cur.fetchone()

    assert row is not None
    # row: id, token_mint, cluster_signal_id, runner_score, verdict, short_circuited, sub_scores_json, explanation_json, created_at
    assert row[1] == "MINT1"
    assert row[2] == 42  # cluster_signal_id
    assert row[4] in ("watch", "strong_candidate", "probable_runner")
    assert row[5] == 0  # short_circuited

    sub_scores = json.loads(row[6])
    assert len(sub_scores) == 9  # 7 dimensions + 2 raw
    assert "raw_rug_risk" in sub_scores
    assert "raw_insider_risk" in sub_scores
    for key in DIMENSION_KEYS:
        assert key in sub_scores

    await db.close()


@pytest.mark.asyncio
async def test_persist_short_circuited_candidate(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    fc = FilteredCandidate(
        enriched=_enriched(),
        filter_results=[FilterResult("rug_gate", False, "lp not locked", {"rug_risk": 0}, {})],
        gate_passed=False, hard_fail_reason="lp not locked", hard_fail_filter_name="rug_gate",
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT verdict, short_circuited, explanation_json FROM runner_scores") as cur:
        row = await cur.fetchone()

    assert row[0] == "ignore"
    assert row[1] == 1
    explanation = json.loads(row[2])
    assert explanation["failed_gate"] == "rug_gate"
    assert explanation["short_circuited"] is True

    await db.close()


@pytest.mark.asyncio
async def test_persist_cluster_signal_id_threaded(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    eng = ScoringEngine(
        filtered_bus=asyncio.Queue(), scored_bus=asyncio.Queue(),
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    sig = _signal(signal_id=99)
    fc = FilteredCandidate(
        enriched=_enriched(sig), filter_results=_all_pass_results(),
        gate_passed=True, hard_fail_reason=None,
    )
    sc = eng.score(fc)
    await eng._persist(sc)

    async with db.conn.execute("SELECT cluster_signal_id FROM runner_scores") as cur:
        row = await cur.fetchone()

    assert row[0] == 99

    await db.close()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd meme-trading && python -m pytest runner/tests/unit/test_scoring_persist.py -v`

Expected: All 3 tests pass.

- [ ] **Step 3: Commit**

```bash
git add meme-trading/runner/tests/unit/test_scoring_persist.py
git commit -m "runner: scoring engine persistence tests"
```

---

### Task 8: Wire `ScoringEngine` into `main.py`

**Files:**
- Modify: `meme-trading/runner/main.py`

- [ ] **Step 1: Replace `_drain_filtered` with `ScoringEngine` + `_drain_scored`**

In `meme-trading/runner/main.py`:

Add import at the top:

```python
from runner.scoring.engine import ScoringEngine
from runner.scoring.models import ScoredCandidate
```

After the `filter_pipeline` setup (around line 121), add:

```python
    scored_bus: asyncio.Queue = asyncio.Queue()
    scoring_engine = ScoringEngine(
        filtered_bus=filtered_bus,
        scored_bus=scored_bus,
        weights=weights,
        tier_cache=tier_cache,
        db=db,
    )
```

In the `asyncio.gather(...)` block, replace:

```python
            _supervise(lambda: _drain_filtered(filtered_bus, logger), "drain_filtered", logger),
```

with:

```python
            _supervise(scoring_engine.run, "scoring_engine", logger),
            _supervise(lambda: _drain_scored(scored_bus, logger), "drain_scored", logger),
```

Update the task names list in the `zip(...)` to match:

```python
        for name, result in zip(
            ["monitor", "detector", "enricher", "filter_pipeline", "scoring_engine", "drain_scored"],
            results,
        ):
```

Replace `_drain_filtered` function with `_drain_scored`:

```python
async def _drain_scored(scored_bus: asyncio.Queue, logger) -> None:
    """Temporary sink: log every scored candidate. Replaced by executor in Plan 3."""
    while True:
        try:
            sc: ScoredCandidate = await scored_bus.get()
            logger.info(
                "scored_candidate_drained",
                mint=sc.filtered.enriched.token_mint,
                symbol=sc.filtered.enriched.symbol,
                score=round(sc.runner_score, 2),
                verdict=sc.verdict,
                short_circuited=sc.explanation.get("short_circuited", False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_scored_iteration_error", error=str(e))
```

- [ ] **Step 2: Run all tests to verify no regressions**

Run: `cd meme-trading && python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -40`

Expected: All tests pass (118 original + new scoring tests).

- [ ] **Step 3: Verify imports are clean**

Run: `cd meme-trading && python -c "from runner.main import _main; print('imports ok')"`

Expected: `imports ok`

- [ ] **Step 4: Commit**

```bash
git add meme-trading/runner/main.py
git commit -m "runner: wire ScoringEngine into main.py, replace _drain_filtered"
```

---

### Task 9: Integration test — filter pipeline through scoring

**Files:**
- Create: `meme-trading/runner/tests/integration/test_filters_to_scoring.py`

- [ ] **Step 1: Write integration test**

Create `meme-trading/runner/tests/integration/test_filters_to_scoring.py`:

```python
"""Integration: FilterPipeline → ScoringEngine end-to-end flow."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.config.weights_loader import WeightsLoader
from runner.db.database import Database
from runner.enrichment.schemas import EnrichedToken
from runner.filters.base import BaseFilter, FilterResult
from runner.filters.pipeline import FilterPipeline
from runner.scoring.engine import ScoringEngine
from runner.scoring.models import DIMENSION_KEYS

import yaml
from pathlib import Path


def _weights_file(tmp_path: Path) -> Path:
    data = {
        "weights": {
            "wallet_quality": 0.20, "cluster_quality": 0.15, "entry_quality": 0.15,
            "holder_quality": 0.15, "rug_risk": 0.15, "follow_through": 0.15, "narrative": 0.05,
        },
        "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78},
        "scoring": {
            "rug_insider_rug_weight": 0.70, "insider_cap_threshold": 25,
            "insider_cap_value": 35, "neutral_fallback": 50, "version": "v1",
            "reload_interval_sec": 30,
        },
        "cluster": {"speed_bonus_sweet_spot_min": 10, "speed_bonus_sweet_spot_max": 20},
    }
    p = tmp_path / "weights.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p


def _tier_cache_stub():
    class _Stub(WalletTierCache):
        def __init__(self):
            self._map = {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    return _Stub()


class _StubFilter(BaseFilter):
    def __init__(self, name: str, result: FilterResult):
        self.name = name  # type: ignore[misc]
        self._result = result

    async def apply(self, enriched):
        return self._result


def _enriched(mint="MINT1") -> EnrichedToken:
    base = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
    sig = ClusterSignal(
        token_mint=mint, wallets=["A1", "A2", "B1"], wallet_count=3,
        tier_counts={"A": 2, "B": 1}, first_buy_time=base,
        last_buy_time=base + timedelta(minutes=14), convergence_seconds=840,
        mid_price_sol=0.0005, id=7,
    )
    return EnrichedToken(
        token_mint=mint, cluster_signal=sig,
        enriched_at=base + timedelta(minutes=15),
        price_sol=0.0006, cluster_signal_id=7,
    )


@pytest.mark.asyncio
async def test_filter_to_scoring_end_to_end(tmp_path):
    """Push an enriched token through filters + scoring, verify DB rows."""
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()
    scored_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug_gate", FilterResult("rug_gate", True, None, {"rug_risk": 85.0}, {})),
        _StubFilter("holder_filter", FilterResult("holder_filter", True, None, {"holder_quality": 55.0}, {})),
        _StubFilter("insider_filter", FilterResult("insider_filter", True, None, {"insider_risk": 70.0}, {})),
        _StubFilter("entry_quality", FilterResult("entry_quality", True, None, {"entry_quality": 80.0}, {})),
    ]
    probe = _StubFilter("follow_through", FilterResult("follow_through", True, None, {"follow_through": 65.0}, {}))

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus, filtered_bus=filtered_bus,
        sync_filters=filters, probe_filter=probe, db=db,
    )

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    # Push enriched token through pipeline
    await pipeline._process_one(_enriched())

    # Manually run one scoring cycle
    fc = await filtered_bus.get()
    sc = scoring.score(fc)
    await scoring._persist(sc)
    await scored_bus.put(sc)

    # Verify filter_results in DB
    async with db.conn.execute(
        "SELECT COUNT(*) FROM filter_results WHERE token_mint='MINT1'"
    ) as cur:
        count = (await cur.fetchone())[0]
    assert count == 5  # 4 sync + 1 probe

    # Verify runner_scores in DB
    async with db.conn.execute(
        "SELECT runner_score, verdict, cluster_signal_id, short_circuited, sub_scores_json FROM runner_scores WHERE token_mint='MINT1'"
    ) as cur:
        row = await cur.fetchone()

    assert row is not None
    assert row[0] > 0  # runner_score > 0
    assert row[1] in ("watch", "strong_candidate", "probable_runner")
    assert row[2] == 7  # cluster_signal_id threaded
    assert row[3] == 0  # not short-circuited

    sub_scores = json.loads(row[4])
    assert len(sub_scores) == 9
    for key in DIMENSION_KEYS:
        assert key in sub_scores

    # Verify scored candidate on bus
    sc_out = scored_bus.get_nowait()
    assert sc_out.runner_score == sc.runner_score

    await db.close()


@pytest.mark.asyncio
async def test_filter_to_scoring_short_circuit(tmp_path):
    """Hard gate failure flows through to scoring as ignore."""
    db = Database(tmp_path / "r.db")
    await db.connect()

    enriched_bus: asyncio.Queue = asyncio.Queue()
    filtered_bus: asyncio.Queue = asyncio.Queue()
    scored_bus: asyncio.Queue = asyncio.Queue()

    filters = [
        _StubFilter("rug_gate", FilterResult("rug_gate", False, "lp not locked", {"rug_risk": 0}, {})),
        _StubFilter("holder_filter", FilterResult("holder_filter", True, None, {"holder_quality": 55.0}, {})),
    ]

    pipeline = FilterPipeline(
        enriched_bus=enriched_bus, filtered_bus=filtered_bus,
        sync_filters=filters, probe_filter=None, db=db,
    )

    scoring = ScoringEngine(
        filtered_bus=filtered_bus, scored_bus=scored_bus,
        weights=WeightsLoader(_weights_file(tmp_path)),
        tier_cache=_tier_cache_stub(), db=db,
    )

    await pipeline._process_one(_enriched())
    fc = await filtered_bus.get()
    sc = scoring.score(fc)
    await scoring._persist(sc)

    assert sc.runner_score == 0.0
    assert sc.verdict == "ignore"

    async with db.conn.execute(
        "SELECT verdict, short_circuited FROM runner_scores WHERE token_mint='MINT1'"
    ) as cur:
        row = await cur.fetchone()
    assert row[0] == "ignore"
    assert row[1] == 1

    await db.close()
```

- [ ] **Step 2: Run integration test**

Run: `cd meme-trading && python -m pytest runner/tests/integration/test_filters_to_scoring.py -v`

Expected: Both tests pass.

- [ ] **Step 3: Run full test suite**

Run: `cd meme-trading && python -m pytest runner/tests/ -v --tb=short 2>&1 | tail -40`

Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add meme-trading/runner/tests/integration/test_filters_to_scoring.py
git commit -m "runner: filter→scoring integration test with DB verification"
```

---

### Task 10: Final push + test count verification

**Files:** None new.

- [ ] **Step 1: Run full test suite and count**

Run: `cd meme-trading && python -m pytest runner/tests/ -v 2>&1 | tail -5`

Expected: All tests pass. Count should be ~153 (118 existing + ~35 new).

- [ ] **Step 2: Push all commits**

```bash
git push
```

- [ ] **Step 3: Verify clean import**

Run: `cd meme-trading && python -c "from runner.scoring.engine import ScoringEngine; from runner.scoring.models import ScoredCandidate, DIMENSION_KEYS; print(f'OK: {len(DIMENSION_KEYS)} dimensions')"`

Expected: `OK: 7 dimensions`

---

## Summary

| Task | What it does | New tests |
|------|-------------|-----------|
| 1 | Thread `cluster_signal_id` + `hard_fail_filter_name` upstream | 0 (existing pass) |
| 2 | Schema migration + weights.yaml scoring section | 1 |
| 3 | `ScoredCandidate` model + `DIMENSION_KEYS` | 3 |
| 4 | `ScoringEngine` — verdict, combine, weight validation | 7 |
| 5 | Dimension derivation tests | 13 |
| 6 | Short-circuit + explanation + full score tests | 8 |
| 7 | Persistence tests | 3 |
| 8 | Wire into `main.py` | 0 |
| 9 | Integration test | 2 |
| 10 | Final push + verification | 0 |

**Total: 10 tasks, ~37 new tests, ~10 commits**
