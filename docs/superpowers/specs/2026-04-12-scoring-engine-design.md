# Scoring Engine (Plan 2c) — Design Spec

**Status:** Approved
**Author:** Claude (with Rich)
**Date:** 2026-04-12
**Parent spec:** `docs/superpowers/specs/2026-04-11-meme-runner-design.md`

---

## 1. Purpose

Add a scoring engine to the runner intelligence pipeline that takes filtered candidates, computes a weighted Runner Score (0-100), assigns a verdict, persists everything with full explainability, and emits scored candidates for downstream consumption (executor, alerts, dashboard in Plan 3+).

## 2. Scope

**In scope:**
- `ScoringEngine` class — single-file, single-class design
- `ScoredCandidate` dataclass
- 7-dimension scoring model with hot-reloadable weights
- Rug/insider combined dimension with weighted average + severe insider cap
- Verdict assignment (ignore / watch / strong_candidate / probable_runner)
- Full explanation persistence for analysis, alerts, and dashboard
- Pipeline integration: replaces `_drain_filtered`, reads `filtered_bus`, writes `scored_bus`
- Thread `cluster_signal_id` through the pipeline
- Schema migration for `short_circuited` column
- Comprehensive test suite

**Out of scope:**
- Paper executor (Plan 3)
- Telegram alerts (Plan 3)
- Dashboard display of scores (Plan 3+)
- Narrative scoring beyond placeholder (future)

## 3. Architecture decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Class structure | Single `ScoringEngine` class in `scoring/engine.py` | One pipeline step, ~150 lines of logic. Four thin classes would be over-abstracted. Split later if complexity grows. |
| Pipeline integration | Bus consumer replacing `_drain_filtered` | Consistent with existing queue-based pipeline pattern. Each stage reads from one queue, writes to the next. |
| Rug/insider merge | Weighted average, 70/30 default | Tunable via weights.yaml. Insiders get meaningful influence without dominating. Severe insider cap prevents a clean rug score from hiding ugly insider structure. |

## 4. Data flow

```
filtered_bus
    |
    v
ScoringEngine.run()   -- long-lived async consumer
    |
    |-- gate_passed=False? --> score=0, verdict="ignore", preserve failure reason
    |-- gate_passed=True?  --> derive 7 dimensions, combine, assign verdict
    |
    |-- INSERT INTO runner_scores
    |
    v
scored_bus
    |
    v
_drain_scored()       -- temporary sink (replaced by executor in Plan 3)
```

## 5. Data model

### ScoredCandidate — `scoring/models.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from runner.filters.base import FilteredCandidate

Verdict = Literal["ignore", "watch", "strong_candidate", "probable_runner"]

@dataclass(frozen=True, eq=False)
class ScoredCandidate:
    filtered: FilteredCandidate
    runner_score: float                 # 0-100
    verdict: Verdict
    dimension_scores: dict[str, float]  # 7 keys, always present, each 0-100
    explanation: dict[str, Any]         # full breakdown for DB + alerts
    scored_at: datetime
```

`dimension_scores` always has all 7 keys, even for short-circuited candidates (zeroed). Keys match `weights.yaml` weight keys exactly: `wallet_quality`, `cluster_quality`, `entry_quality`, `holder_quality`, `rug_risk`, `follow_through`, `narrative`.

## 6. ScoringEngine — `scoring/engine.py`

### Constructor

```python
class ScoringEngine:
    def __init__(
        self,
        filtered_bus: asyncio.Queue,
        scored_bus: asyncio.Queue,
        weights: WeightsLoader,
        tier_cache: WalletTierCache,
        db: Database | None = None,
    ):
```

- `weights` — read-only during `score()`. Hot-reload happens in `run()` loop with a TTL guard (at most once per 30s). On reload, validates that dimension weights sum to ~1.0 (tolerance 0.01) — logs a loud warning and keeps previous valid weights if validation fails.
- `tier_cache` — required for wallet quality derivation (mean tier points of cluster wallets).
- `db` — optional, for persistence. Scoring works without it (tests).
- `_reload_interval_sec` — configurable TTL for weight reload checks (default 30).
- On startup, validates weights immediately. Fails fast if initial weights are invalid.

### Public interface

**`run()`** — long-lived async loop:
1. Read `FilteredCandidate` from `filtered_bus`
2. Check weights reload with a TTL guard — at most once every 30 seconds (configurable). Track `_last_reload_check: float` and only call `weights.check_and_reload()` when `time.monotonic() - _last_reload_check >= reload_interval_sec`.
3. Validate weights on reload — if dimension weights don't sum to ~1.0 (tolerance 0.01), log a loud warning but continue with previous valid weights.
4. Call `score(fc)` (pure, no I/O)
5. Persist to `runner_scores` table
6. Emit `ScoredCandidate` onto `scored_bus`

**`score(fc) -> ScoredCandidate`** — pure scoring logic:
- No async, no DB, no side effects
- Consumes already-loaded weights (does NOT call `check_and_reload()`)
- Primary unit test target

### Private methods

**`_derive_dimensions(fc) -> dict[str, float]`** — builds all 7 dimension scores.

**`_combine_scores(dimensions) -> float`** — weighted sum using `weights.yaml` values.

**`_assign_verdict(score) -> Verdict`** — maps score to verdict using threshold bands.

**`_build_explanation(fc, dimensions, score, verdict) -> dict`** — builds full explanation dict.

## 7. Dimension derivation

### Direct filter lookups (3 of 7)

| Dimension | filter_name | sub_scores key | Missing data fallback |
|---|---|---|---|
| `entry_quality` | `"entry_quality"` | `"entry_quality"` | neutral_fallback (default 50) |
| `holder_quality` | `"holder_filter"` | `"holder_quality"` | neutral_fallback (default 50) |
| `follow_through` | `"follow_through"` | `"follow_through"` | neutral_fallback (default 50) |

**Missing data handling:** If a sub-score is absent (API failure during filtering, filter crashed), use `scoring.neutral_fallback` from weights.yaml (default 50). Record `data_degraded: true` and `missing_subscores: [...]` in explanation. Do NOT default to 0.0 for missing data — 0.0 is reserved for true failed conditions.

### Rug risk (combined, 1 of 7)

```python
raw_rug = lookup("rug_gate", "rug_risk")           # 0-100, neutral fallback if missing
raw_insider = lookup("insider_filter", "insider_risk")  # 0-100, neutral fallback if missing

rug_weight = weights.get("scoring.rug_insider_rug_weight", 0.70)
insider_weight = 1.0 - rug_weight

rug_risk = rug_weight * raw_rug + insider_weight * raw_insider

# Severe insider override — ugly insiders cannot be hidden by clean rug score
insider_cap_threshold = weights.get("scoring.insider_cap_threshold", 25)
insider_cap_value = weights.get("scoring.insider_cap_value", 35)
if raw_insider < insider_cap_threshold:
    rug_risk = min(rug_risk, insider_cap_value)
```

Both `raw_rug` and `raw_insider` are persisted separately in `sub_scores_json` for analysis.

### Wallet quality (derived, 1 of 7)

Mean tier points of wallets in the cluster:

```python
wallets = fc.filtered.enriched.cluster_signal.wallets
points = [tier_cache.tier_of(w).points for w in wallets]
# Tier.A=100, Tier.B=60, Tier.U=40 (unknown/default), Tier.C=0
wallet_quality = mean(points) if points else neutral_fallback
wallet_quality = max(0.0, min(100.0, wallet_quality))
```

- Unknown wallets default to Tier.U (40 points) via `WalletTierCache.tier_of()` — already implemented.
- C-tier wallets shouldn't appear (filtered at convergence), but their 0 points drag the mean naturally if they do.
- Empty wallet list returns neutral fallback and sets `data_degraded: true`.

### Cluster quality (derived heuristic, 1 of 7)

Heuristic v1 formula — expect tuning from real observed results:

```python
base = 50
wallet_bonus = min((wallet_count - 3) * 10, 30)   # +10 per extra, cap +30

convergence_minutes = convergence_seconds / 60
sweet_min = weights.get("cluster.speed_bonus_sweet_spot_min", 10)
sweet_max = weights.get("cluster.speed_bonus_sweet_spot_max", 20)

if sweet_min <= convergence_minutes <= sweet_max:
    speed_bonus = 20    # validated sweet spot
elif 5 <= convergence_minutes < sweet_min:
    speed_bonus = 10
elif sweet_max < convergence_minutes <= 30:
    speed_bonus = 10
elif convergence_minutes < 5:
    speed_bonus = -20   # possible bundle/coordinated
else:
    speed_bonus = 0     # >30 min, shouldn't happen (window enforced)

cluster_quality = max(0, min(100, base + wallet_bonus + speed_bonus))
```

All parameters configurable. Marked as heuristic — not statistically validated.

### Narrative (placeholder, 1 of 7)

```python
narrative = 50  # neutral placeholder, NOT a real signal
```

Weight is 0.05 in weights.yaml. Can be set to 0.0 to disable entirely until real narrative scoring is implemented.

## 8. Score combination

```python
def _combine_scores(self, dimensions: dict[str, float]) -> float:
    total = 0.0
    for key, score in dimensions.items():
        weight = self.weights.get(f"weights.{key}", 0.0)
        total += weight * score
    return max(0.0, min(100.0, total))
```

Weights from `weights.yaml`:

```yaml
weights:
  wallet_quality:   0.20
  cluster_quality:  0.15
  entry_quality:    0.15
  holder_quality:   0.15
  rug_risk:         0.15
  follow_through:   0.15
  narrative:        0.05
```

Weights must sum to 1.0. No runtime validation — operator responsibility via config.

## 9. Verdict assignment

```python
def _assign_verdict(self, score: float) -> Verdict:
    thresholds = {
        "probable_runner":  self.weights.get("verdict_thresholds.probable_runner", 78),
        "strong_candidate": self.weights.get("verdict_thresholds.strong_candidate", 60),
        "watch":            self.weights.get("verdict_thresholds.watch", 40),
    }
    if score >= thresholds["probable_runner"]:
        return "probable_runner"
    if score >= thresholds["strong_candidate"]:
        return "strong_candidate"
    if score >= thresholds["watch"]:
        return "watch"
    return "ignore"
```

All thresholds from weights.yaml. Any hard gate failure → "ignore" regardless of score.

## 10. Short-circuited candidates

For `gate_passed=False`:

```python
runner_score = 0.0
verdict = "ignore"
dimension_scores = {k: 0.0 for k in DIMENSION_KEYS}  # all 7 keys present
explanation = {
    "scoring_version": weights.get("scoring.version", "v1"),
    "weights_mtime": weights.last_loaded_mtime,
    "short_circuited": True,
    "data_degraded": False,
    "missing_subscores": [],
    "failed_gate": fc.hard_fail_filter_name,            # e.g. "rug_gate" (explicit field, not index-derived)
    "failed_reason": fc.hard_fail_reason,              # e.g. "mint authority not revoked"
    "dimensions": {k: {"score": 0, "weight": w, "weighted": 0, "detail": {}} for ...},
    "verdict_thresholds": {...}
}
```

All 7 dimension keys always present (zeroed). Downstream consumers never need to check for missing keys.

## 11. Explanation JSON structure

### Normal candidate

```json
{
  "scoring_version": "v1",
  "weights_mtime": 1744451400.0,
  "weights_hash": "a3f2c1",
  "short_circuited": false,
  "data_degraded": false,
  "missing_subscores": [],
  "failed_gate": null,
  "failed_reason": null,
  "dimensions": {
    "wallet_quality": {
      "score": 73.3,
      "weight": 0.20,
      "weighted": 14.66,
      "detail": {
        "wallets": 3,
        "tiers": ["A", "B", "U"],
        "points": [100, 60, 40]
      }
    },
    "cluster_quality": {
      "score": 80,
      "weight": 0.15,
      "weighted": 12.0,
      "detail": {
        "wallet_count": 4,
        "convergence_minutes": 14.0,
        "speed_bonus": 20,
        "wallet_bonus": 10
      }
    },
    "entry_quality": {
      "score": 75,
      "weight": 0.15,
      "weighted": 11.25,
      "detail": {}
    },
    "holder_quality": {
      "score": 60,
      "weight": 0.15,
      "weighted": 9.0,
      "detail": {}
    },
    "rug_risk": {
      "score": 72.5,
      "weight": 0.15,
      "weighted": 10.875,
      "detail": {
        "raw_rug": 85.0,
        "raw_insider": 50.0,
        "rug_weight": 0.7,
        "insider_capped": false
      }
    },
    "follow_through": {
      "score": 60,
      "weight": 0.15,
      "weighted": 9.0,
      "detail": {}
    },
    "narrative": {
      "score": 50,
      "weight": 0.05,
      "weighted": 2.5,
      "detail": {"placeholder": true}
    }
  },
  "verdict_thresholds": {
    "watch": 40,
    "strong_candidate": 60,
    "probable_runner": 78
  }
}
```

### Short-circuited candidate

```json
{
  "scoring_version": "v1",
  "weights_mtime": 1744451400.0,
  "short_circuited": true,
  "data_degraded": false,
  "missing_subscores": [],
  "failed_gate": "rug_gate",
  "failed_reason": "mint authority not revoked",
  "dimensions": {
    "wallet_quality": {"score": 0, "weight": 0.20, "weighted": 0, "detail": {}},
    "cluster_quality": {"score": 0, "weight": 0.15, "weighted": 0, "detail": {}},
    "entry_quality": {"score": 0, "weight": 0.15, "weighted": 0, "detail": {}},
    "holder_quality": {"score": 0, "weight": 0.15, "weighted": 0, "detail": {}},
    "rug_risk": {"score": 0, "weight": 0.15, "weighted": 0, "detail": {}},
    "follow_through": {"score": 0, "weight": 0.15, "weighted": 0, "detail": {}},
    "narrative": {"score": 0, "weight": 0.05, "weighted": 0, "detail": {}}
  },
  "verdict_thresholds": {"watch": 40, "strong_candidate": 60, "probable_runner": 78}
}
```

## 12. Persistence

### `runner_scores` table — updated schema

```sql
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

Query-friendly columns outside JSON: `token_mint`, `cluster_signal_id`, `runner_score`, `verdict`, `short_circuited`, `created_at`. Dashboard and analytics can filter without parsing JSON.

### `sub_scores_json` content

Always 9 keys: 7 dimension scores + 2 raw component scores.

```json
{
  "wallet_quality": 73.3,
  "cluster_quality": 80,
  "entry_quality": 75,
  "holder_quality": 60,
  "rug_risk": 72.5,
  "follow_through": 60,
  "narrative": 50,
  "raw_rug_risk": 85,
  "raw_insider_risk": 50
}
```

### Schema migration

For existing databases that already have the `runner_scores` table without `short_circuited`:

- Check whether `runner_scores.short_circuited` column exists
- If not, run: `ALTER TABLE runner_scores ADD COLUMN short_circuited INTEGER DEFAULT 0;`
- Fresh databases get it from the base schema definition

Migration runs in `Database._ensure_schema()` after the base `CREATE TABLE IF NOT EXISTS` statements.

### `cluster_signal_id` threading

Add `id: int | None = None` to `ClusterSignal` dataclass. Populate from `lastrowid` after INSERT in `convergence.py`. Thread through:

```
ClusterSignal.id → EnrichedToken.cluster_signal_id → FilteredCandidate (via enriched) → ScoredCandidate (via filtered)
```

Small upstream changes:
- `ClusterSignal`: add `id: int | None = None` field
- `EnrichedToken`: add `cluster_signal_id: int | None = None` field
- `FilteredCandidate`: add `hard_fail_filter_name: str | None = None` field. Set explicitly in `FilterPipeline._process_one()` when a gate fails, rather than deriving from filter_results index. This guarantees the failing filter name is preserved regardless of result ordering.
- `convergence.py`: read `lastrowid` after INSERT. Since `ClusterSignal` is `frozen=True`, use `dataclasses.replace(signal, id=lastrowid)` to produce the copy with the ID set before emitting to `signal_bus`.
- `enricher.py`: pass `cluster_signal.id` → `EnrichedToken.cluster_signal_id`
- `pipeline.py`: use `fc.enriched.cluster_signal_id` instead of hardcoded `None`; set `hard_fail_filter_name=f.name` on gate failure

## 13. weights.yaml additions

New `scoring` section:

```yaml
scoring:
  rug_insider_rug_weight: 0.70       # rug_risk share in combined rug dimension
  insider_cap_threshold: 25          # insider_risk below this triggers cap
  insider_cap_value: 35              # combined rug_risk capped at this
  neutral_fallback: 50               # default for missing/degraded sub-scores
  version: "v1"                      # persisted in explanation for comparison
```

No changes to existing keys (`weights`, `verdict_thresholds`, `cluster`, `gates`, etc.).

### Version markers persisted per candidate

Three version markers in every `explanation_json`:
- `scoring_version` — from `scoring.version` in weights.yaml (e.g. `"v1"`)
- `weights_mtime` — file modification time of weights.yaml at last reload
- `weights_hash` — short hash (first 6 chars of MD5) of the loaded scoring config section, for detecting config drift between candidates

## 14. main.py wiring

Replace `_drain_filtered` with `ScoringEngine`:

```python
# New
scored_bus: asyncio.Queue = asyncio.Queue()
scoring_engine = ScoringEngine(
    filtered_bus=filtered_bus,
    scored_bus=scored_bus,
    weights=weights,
    tier_cache=tier_cache,
    db=db,
)

# In asyncio.gather:
_supervise(scoring_engine.run, "scoring_engine", logger),
_supervise(lambda: _drain_scored(scored_bus, logger), "drain_scored", logger),
```

`_drain_scored` is a temporary sink that logs every scored candidate with verdict and score. Replaced by executor in Plan 3.

## 15. File layout

```
meme-trading/runner/
  scoring/
    __init__.py
    models.py         # ScoredCandidate, Verdict type alias
    engine.py         # ScoringEngine (single class, all scoring logic)
  tests/
    unit/
      test_scoring_engine.py    # pure scoring tests
      test_scoring_persist.py   # DB persistence tests
    integration/
      test_filters_to_scoring.py  # end-to-end filter→score flow
```

## 16. Test strategy

### Layer 1: Unit tests — `test_scoring_engine.py`

Pure function tests against `ScoringEngine.score()`. No DB, no queues.

| Test | What it verifies |
|---|---|
| All 4 verdict tiers | Score ranges map correctly to verdicts |
| Short-circuited candidate | gate_passed=False → score=0, verdict=ignore, failure reason preserved |
| Missing sub-scores | Neutral fallback (50) used, data_degraded=true, missing_subscores populated |
| Rug/insider combine without cap | 70/30 weighted average produces expected value |
| Rug/insider combine with cap | insider_risk < 25 caps combined score at 35 |
| Wallet quality — mixed tiers | Mean of A(100) + B(60) + U(40) = 66.7 |
| Wallet quality — all A-tier | Score = 100 |
| Wallet quality — all U-tier | Score = 40 |
| Wallet quality — unknown wallets | Unknown wallets use Tier.U default (40), no crash |
| Cluster quality — sweet spot speed | 10-20 min convergence gets +20 bonus |
| Cluster quality — fast convergence | < 5 min gets -20 penalty (possible bundle) |
| Cluster quality — extra wallets | +10 per wallet above 3, capped at +30 |
| Narrative placeholder | Always 50, weight 0.05 |
| Explanation structure | All 7 dimension keys present, scoring_version and weights_mtime included |
| Weights hot-reload | `score()` uses current weights, does NOT call check_and_reload |

### Layer 2: Persistence tests — `test_scoring_persist.py`

Mock/real DB. Verify:

| Test | What it verifies |
|---|---|
| Normal candidate persisted | INSERT with correct columns, sub_scores_json has 9 keys |
| Short-circuited persisted | short_circuited=1, verdict="ignore", explanation has failed_gate |
| cluster_signal_id threaded | Non-null ID carried from ClusterSignal through to runner_scores row |
| Schema migration | short_circuited column added to existing table without the column |

### Layer 3: Integration test — `test_filters_to_scoring.py`

End-to-end flow: build an EnrichedToken, push through FilterPipeline → ScoringEngine, verify:
- `runner_scores` row exists with correct verdict
- `filter_results` rows exist for all filters
- `cluster_signal_id` links correctly

---

**End of spec.**
