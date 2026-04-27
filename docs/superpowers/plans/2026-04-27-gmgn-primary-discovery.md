# GMGN-Primary Wallet Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Nansen-only wallet discovery with GMGN-Apify as the primary source, keep Nansen as an optional secondary, prune dead-weight wallets, and unblock fast convergence signals — all in `smc-trading`.

**Architecture:** `CurationPipeline.run_once()` is rewritten to (1) prune auto-source wallets with no buy_events in N days, (2) discover via two GMGN-Apify trader-type buckets and score with `GMGNRanker`, (3) call Nansen only if a key is configured, (4) cap new wallets per cycle so the pool stays stable. Cadence moves from 6h → 12h. Convergence-speed floor drops from 10min → 0min so fast-conviction signals (currently 100% rejected) can finally trade.

**Tech Stack:** Python 3.12, pydantic-settings, httpx, aiosqlite, pytest + pytest-asyncio, Apify, GMGN.

---

## Empirical context (from VPS DB audit, 22d window 2026-04-05 → 2026-04-27)

- 358 nansen-live wallets → 1,796 buys (5/wallet)
- 48 gmgn-apify wallets → 4,844 buys (100/wallet — **20× per-capita**)
- 4 birdeye-bulk wallets → 25 buys (irrelevant)
- 1,158 convergence signals fired; 103 paper trades; **−1.044 SOL** (avg −14.17%, 18% win rate)
- The convergence-speed filter `[10–20]min` rejects fast signals (e.g. `3.5min — outside window`). We have **zero** trade data on fast convergence because the filter has rejected every single one.

## Files

- **Modify** `meme-trading/config/settings.py` — defaults + new GMGN/prune knobs
- **Modify** `meme-trading/curation/pipeline.py` — rewrite `run_once`, add `_discover_gmgn_apify` + `_prune_dead_wallets`, fix hardcoded `source` in `_merge_wallets`
- **Create** `meme-trading/tests/__init__.py`
- **Create** `meme-trading/tests/conftest.py` — pytest path setup
- **Create** `meme-trading/tests/test_curation_pipeline.py` — unit tests for new flow
- **Modify** `meme-trading/requirements.txt` — add pytest deps (dev)
- **Modify** VPS `/docker/smc-trading/.env` — propagate new env values
- **Update memory** `~/.claude/projects/C--Users-rakai-Leverage/memory/project_runner_cook_mode.md` — note cook window verdict and that we acted on it

---

### Task 1: Settings — new fields and defaults

**Files:**
- Modify: `meme-trading/config/settings.py`

- [ ] **Step 1: Add GMGN/prune fields and change two defaults**

Edit `meme-trading/config/settings.py` — replace the `# Curation` block (lines 78–82) with:

```python
    # Curation cadence
    curation_interval_hours: float = 12.0  # was 6.0 — sufficient given pool stability

    # Wallet score floors (legacy / Nansen path)
    min_wallet_winrate: float = 0.55
    min_wallet_pnl_sol: float = 5.0
    min_wallet_score: float = 40.0

    # GMGN-Apify discovery
    gmgn_min_score: float = 70.0          # composite threshold from GMGNRanker
    gmgn_min_winrate_pct: int = 50        # passed to Apify copytrade scraper
    gmgn_min_txs_7d: int = 10             # bot/dormant filter
    gmgn_max_per_actor: int = 100         # cap each Apify actor pull
    gmgn_max_new_per_cycle: int = 20      # cap NEW additions per cycle (pool stability)

    # Stale-wallet pruning
    wallet_prune_dead_days: int = 7       # auto-source wallets with 0 buys in N days are deactivated
```

And change one existing field:

```python
    # Convergence speed filter (minutes between first buy and signal)
    min_convergence_minutes: float = 0.0   # was 10.0 — fast convergence is high-conviction
    max_convergence_minutes: float = 20.0
```

- [ ] **Step 2: Verify pydantic still loads**

Run from `meme-trading/`:

```bash
python -c "from config.settings import Settings; s = Settings(); print(s.curation_interval_hours, s.gmgn_min_score, s.min_convergence_minutes)"
```

Expected: `12.0 70.0 0.0`

- [ ] **Step 3: Commit**

```bash
git add meme-trading/config/settings.py
git commit -m "smc: settings — GMGN discovery knobs, 12h cadence, drop convergence-speed floor"
```

---

### Task 2: Test scaffolding

**Files:**
- Create: `meme-trading/tests/__init__.py`
- Create: `meme-trading/tests/conftest.py`
- Modify: `meme-trading/requirements.txt`

- [ ] **Step 1: Create empty package marker**

Write `meme-trading/tests/__init__.py` with empty content.

- [ ] **Step 2: Create conftest with path setup**

Write `meme-trading/tests/conftest.py`:

```python
"""Shared test fixtures + path setup for SMC trading tests."""
import sys
from pathlib import Path

# Make the meme-trading directory importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
```

- [ ] **Step 3: Add pytest to requirements**

Append to `meme-trading/requirements.txt`:

```
pytest==8.3.0
pytest-asyncio==0.24.0
```

- [ ] **Step 4: Install and verify pytest collects**

```bash
cd meme-trading && pip install pytest==8.3.0 pytest-asyncio==0.24.0
pytest tests/ --collect-only
```

Expected: collects 0 tests, no import errors.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/tests/__init__.py meme-trading/tests/conftest.py meme-trading/requirements.txt
git commit -m "smc: test scaffolding for curation pipeline"
```

---

### Task 3: Fix hardcoded `source` in `_merge_wallets`

The existing merge always writes `"source": "nansen-live"` for new wallets. Per-source provenance must come from the candidate dict so multi-source discovery works.

**Files:**
- Modify: `meme-trading/curation/pipeline.py:139-193`
- Test: `meme-trading/tests/test_curation_pipeline.py`

- [ ] **Step 1: Write the failing test**

Create `meme-trading/tests/test_curation_pipeline.py`:

```python
"""Tests for CurationPipeline."""
import json
from pathlib import Path

import pytest

from config.settings import Settings
from curation.pipeline import CurationPipeline


@pytest.fixture
def tmp_wallets(tmp_path):
    path = tmp_path / "wallets.json"
    path.write_text(json.dumps({
        "wallets": [
            {"address": "MANUAL1", "label": "manual-one", "source": "manual",
             "score": 80, "active": True, "added_at": "2026-01-01T00:00:00+00:00"},
        ],
        "updated_at": "2026-01-01T00:00:00+00:00",
        "version": 1,
    }))
    return path


@pytest.fixture
def settings(tmp_wallets):
    s = Settings()
    s.wallets_json_path = str(tmp_wallets)
    return s


@pytest.mark.asyncio
async def test_merge_preserves_provided_source(settings, tmp_wallets):
    """A new candidate carrying source=gmgn-apify must land with that source, not 'nansen-live'."""
    pipeline = CurationPipeline(settings)
    new = [{
        "address": "GMGN1",
        "score": 75,
        "stats": {"total_trades": 12, "win_rate": 60, "total_pnl_sol": 0, "avg_hold_minutes": 0},
        "label_hint": "gmgn-75-wr60-$5k7d",
        "source": "gmgn-apify",
    }]
    added, updated, deactivated = await pipeline._merge_wallets(new)
    assert added == 1
    data = json.loads(tmp_wallets.read_text())
    gmgn = next(w for w in data["wallets"] if w["address"] == "GMGN1")
    assert gmgn["source"] == "gmgn-apify"


@pytest.mark.asyncio
async def test_merge_default_source_when_missing(settings, tmp_wallets):
    """If a candidate has no source field, fall back to 'auto'."""
    pipeline = CurationPipeline(settings)
    new = [{
        "address": "ANON1",
        "score": 65,
        "stats": {"total_trades": 5, "win_rate": 50, "total_pnl_sol": 0, "avg_hold_minutes": 0},
        "label_hint": "anon",
    }]
    await pipeline._merge_wallets(new)
    data = json.loads(tmp_wallets.read_text())
    anon = next(w for w in data["wallets"] if w["address"] == "ANON1")
    assert anon["source"] == "auto"
```

- [ ] **Step 2: Run test, verify failure**

```bash
cd meme-trading && pytest tests/test_curation_pipeline.py::test_merge_preserves_provided_source -v
```

Expected: FAIL — assertion `assert "nansen-live" == "gmgn-apify"`.

- [ ] **Step 3: Fix `_merge_wallets`**

In `meme-trading/curation/pipeline.py`, locate the `else` branch in `_merge_wallets` (around line 162) that creates the new entry. Replace this block:

```python
            else:
                # Add new discovered wallet
                existing[addr] = {
                    "address": addr,
                    "label": nw.get("label_hint", f"auto-{addr[:8]}"),
                    "source": "nansen-live",
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "score": nw["score"],
                    "stats": nw["stats"],
                    "active": True,
                }
                added += 1
```

with:

```python
            else:
                # Add new discovered wallet — provenance from candidate
                existing[addr] = {
                    "address": addr,
                    "label": nw.get("label_hint", f"auto-{addr[:8]}"),
                    "source": nw.get("source", "auto"),
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "score": nw["score"],
                    "stats": nw["stats"],
                    "active": True,
                }
                added += 1
```

- [ ] **Step 4: Run both tests, verify pass**

```bash
pytest tests/test_curation_pipeline.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/tests/test_curation_pipeline.py meme-trading/curation/pipeline.py
git commit -m "smc: pipeline merge respects per-candidate source"
```

---

### Task 4: GMGN-Apify discovery method

**Files:**
- Modify: `meme-trading/curation/pipeline.py`
- Test: `meme-trading/tests/test_curation_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `meme-trading/tests/test_curation_pipeline.py`:

```python
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_discover_gmgn_apify_no_token_returns_empty(settings):
    settings.apify_api_token = ""
    pipeline = CurationPipeline(settings)
    result = await pipeline._discover_gmgn_apify()
    assert result == []


@pytest.mark.asyncio
async def test_discover_gmgn_apify_filters_by_score(settings):
    """Candidates failing GMGNRanker.meets_minimum must be excluded."""
    settings.apify_api_token = "test-token"
    settings.gmgn_min_score = 70.0
    settings.gmgn_max_per_actor = 50
    settings.gmgn_max_new_per_cycle = 20

    # Build a high-score and a low-score candidate from Apify
    import time
    now = time.time()
    high = {
        "wallet_address": "HIGH1",
        "winrate_7d": 0.70, "realized_profit_7d": 50000,
        "winrate_30d": 0.65, "realized_profit_30d": 200000,
        "txs_7d": 50, "last_active": now - 3600,
        "pnl_2x_5x_num_7d": 3, "pnl_gt_5x_num_7d": 1,
    }
    low = {
        "wallet_address": "LOW1",
        "winrate_7d": 0.40, "realized_profit_7d": -1000,
        "winrate_30d": 0.45, "realized_profit_30d": -2000,
        "txs_7d": 3, "last_active": now - 3600,
    }

    fake_apify = AsyncMock()
    fake_apify.discover_copytrade_wallets = AsyncMock(side_effect=[[high, low], [high]])

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.ApifyGMGNClient", return_value=fake_apify):
        result = await pipeline._discover_gmgn_apify()

    addrs = [w["address"] for w in result]
    assert "HIGH1" in addrs
    assert "LOW1" not in addrs
    # Source is correctly tagged
    assert all(w["source"] == "gmgn-apify" for w in result)
    # max_new_per_cycle cap applied — duplicate HIGH1 dedupes to one entry
    assert len(result) == 1


@pytest.mark.asyncio
async def test_discover_gmgn_apify_caps_new_per_cycle(settings):
    """Even with many qualified candidates, cap new additions per cycle."""
    settings.apify_api_token = "test-token"
    settings.gmgn_min_score = 60.0
    settings.gmgn_max_new_per_cycle = 3

    import time
    now = time.time()
    candidates = [
        {
            "wallet_address": f"WALLET{i}",
            "winrate_7d": 0.65, "realized_profit_7d": 5000 + i * 100,
            "winrate_30d": 0.60, "realized_profit_30d": 20000,
            "txs_7d": 20, "last_active": now - 3600,
            "pnl_2x_5x_num_7d": 2,
        }
        for i in range(10)
    ]

    fake_apify = AsyncMock()
    fake_apify.discover_copytrade_wallets = AsyncMock(return_value=candidates)

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.ApifyGMGNClient", return_value=fake_apify):
        result = await pipeline._discover_gmgn_apify()

    assert len(result) == 3
```

- [ ] **Step 2: Run tests, verify failure**

```bash
pytest tests/test_curation_pipeline.py::test_discover_gmgn_apify_no_token_returns_empty -v
```

Expected: FAIL — `AttributeError: '_discover_gmgn_apify' not defined`.

- [ ] **Step 3: Implement `_discover_gmgn_apify`**

In `meme-trading/curation/pipeline.py`:

a) Add imports near the top (after existing imports):

```python
from curation.apify_gmgn import ApifyGMGNClient
from curation.gmgn_ranker import GMGNRanker
```

b) Add this method to `CurationPipeline` (insert after `_discover_nansen`, before `_merge_wallets`):

```python
    async def _discover_gmgn_apify(self) -> list[dict]:
        """Pull profitable wallets from GMGN via Apify (smart_degen + pump_smart buckets).

        Filters candidates through GMGNRanker.meets_minimum, sorts by composite score,
        caps additions at gmgn_max_new_per_cycle to keep the pool stable.
        """
        if not self.settings.apify_api_token:
            logger.warning("No Apify API token — skipping GMGN-Apify discovery")
            return []

        apify = ApifyGMGNClient(self.settings.apify_api_token, self.http)
        ranker = GMGNRanker()

        # Pull two trader-type buckets in parallel
        buckets = ("smart_degen", "pump_smart")
        results = await asyncio.gather(
            *[
                apify.discover_copytrade_wallets(
                    trader_type=tt,
                    sort_by="profit_7days",
                    min_winrate_7d=self.settings.gmgn_min_winrate_pct,
                    min_txs_7d=self.settings.gmgn_min_txs_7d,
                    max_items=self.settings.gmgn_max_per_actor,
                )
                for tt in buckets
            ],
            return_exceptions=True,
        )

        candidates: list[dict] = []
        for tt, items in zip(buckets, results):
            if isinstance(items, Exception):
                logger.error(f"Apify {tt} bucket failed: {items}")
                continue
            candidates.extend(items)
            logger.info(f"GMGN-Apify {tt}: {len(items)} raw")

        # Dedupe by address (same wallet may appear in both buckets)
        seen: dict[str, dict] = {}
        for c in candidates:
            addr = c.get("wallet_address") or c.get("address")
            if addr and addr not in seen:
                seen[addr] = c

        # Score, filter, sort
        scored: list[tuple[float, dict, dict]] = []  # (composite, candidate, score_result)
        for addr, c in seen.items():
            if not ranker.meets_minimum(c, min_composite=self.settings.gmgn_min_score):
                continue
            result = ranker.score(c)
            scored.append((result["composite"], c, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        scored = scored[: self.settings.gmgn_max_new_per_cycle]

        qualified = []
        for composite, c, result in scored:
            addr = c.get("wallet_address") or c.get("address")
            wr_pct = int(float(c.get("winrate_7d", 0) or 0) * 100)
            profit_k = int(float(c.get("realized_profit_7d", 0) or 0) / 1000)
            qualified.append({
                "address": addr,
                "score": composite,
                "stats": {
                    "total_trades": int(c.get("txs_7d", 0) or 0),
                    "win_rate": float(c.get("winrate_7d", 0) or 0) * 100,
                    "total_pnl_sol": 0.0,  # GMGN reports USD, not SOL
                    "total_pnl_usd_7d": float(c.get("realized_profit_7d", 0) or 0),
                    "avg_hold_minutes": 0,
                },
                "label_hint": f"gmgn-{int(composite)}-wr{wr_pct}-${profit_k}k7d",
                "source": "gmgn-apify",
            })

        logger.info(
            f"GMGN-Apify discovery: {len(seen)} unique candidates → "
            f"{len(qualified)} qualified (score≥{self.settings.gmgn_min_score})"
        )
        return qualified
```

- [ ] **Step 4: Run tests, verify pass**

```bash
pytest tests/test_curation_pipeline.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/tests/test_curation_pipeline.py meme-trading/curation/pipeline.py
git commit -m "smc: GMGN-Apify discovery with ranker filter + per-cycle cap"
```

---

### Task 5: Dead-wallet pruning

Auto-source wallets that haven't produced a buy_event in N days are deactivated. Manual wallets are never touched.

**Files:**
- Modify: `meme-trading/curation/pipeline.py`
- Test: `meme-trading/tests/test_curation_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `meme-trading/tests/test_curation_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_prune_dead_wallets_skips_manual_and_inactive(settings, tmp_wallets, tmp_path):
    """Only auto-source, currently-active wallets with 0 buys in window get deactivated."""
    # Replace wallets file with a richer fixture
    tmp_wallets.write_text(json.dumps({
        "wallets": [
            {"address": "MANUAL1", "label": "m", "source": "manual", "active": True, "score": 80,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "GMGN_ACTIVE", "label": "g1", "source": "gmgn-apify", "active": True, "score": 75,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "GMGN_DEAD", "label": "g2", "source": "gmgn-apify", "active": True, "score": 65,
             "added_at": "2026-01-01T00:00:00+00:00"},
            {"address": "ALREADY_OFF", "label": "off", "source": "nansen-live", "active": False, "score": 30,
             "added_at": "2026-01-01T00:00:00+00:00"},
        ],
        "updated_at": "2026-01-01T00:00:00+00:00",
        "version": 1,
    }))

    # Mock the DB query result — only GMGN_ACTIVE has recent buys
    dead_rows = [{"address": "GMGN_DEAD"}]
    fake_db = AsyncMock()
    fake_db.execute_fetchall = AsyncMock(return_value=dead_rows)
    fake_db.execute = AsyncMock()
    fake_db.commit = AsyncMock()

    pipeline = CurationPipeline(settings)
    with patch("curation.pipeline.get_db", AsyncMock(return_value=fake_db)):
        pruned = await pipeline._prune_dead_wallets(days=7)

    assert pruned == 1

    data = json.loads(tmp_wallets.read_text())
    by_addr = {w["address"]: w for w in data["wallets"]}
    assert by_addr["MANUAL1"]["active"] is True
    assert by_addr["GMGN_ACTIVE"]["active"] is True
    assert by_addr["GMGN_DEAD"]["active"] is False
    assert by_addr["ALREADY_OFF"]["active"] is False  # unchanged
```

- [ ] **Step 2: Run test, verify failure**

```bash
pytest tests/test_curation_pipeline.py::test_prune_dead_wallets_skips_manual_and_inactive -v
```

Expected: FAIL — `_prune_dead_wallets` not defined.

- [ ] **Step 3: Implement `_prune_dead_wallets`**

In `meme-trading/curation/pipeline.py`, add this method (before `_sync_to_db`):

```python
    async def _prune_dead_wallets(self, days: int) -> int:
        """Deactivate auto-source wallets with 0 buy_events in the last `days` days.

        Manual wallets are never touched. Returns count of wallets newly deactivated.
        Updates both DB and wallets.json.
        """
        db = await get_db()
        rows = await db.execute_fetchall(
            f"""SELECT w.address FROM tracked_wallets w
                LEFT JOIN buy_events b
                  ON b.wallet_address = w.address
                  AND b.timestamp > datetime('now', '-{int(days)} days')
                WHERE w.active = 1 AND w.source != 'manual'
                GROUP BY w.address
                HAVING COUNT(b.id) = 0"""
        )
        dead = [r["address"] for r in (rows or [])]
        if not dead:
            return 0

        # Deactivate in DB
        placeholders = ",".join("?" for _ in dead)
        await db.execute(
            f"UPDATE tracked_wallets SET active = 0 WHERE address IN ({placeholders})",
            dead,
        )
        await db.commit()

        # Mirror change in wallets.json (preserve order, skip manual defensively)
        path = Path(self.settings.wallets_json_path)
        data = json.loads(path.read_text())
        dead_set = set(dead)
        for w in data.get("wallets", []):
            if w.get("address") in dead_set and w.get("source") != "manual":
                w["active"] = False
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        data["version"] = data.get("version", 0) + 1
        path.write_text(json.dumps(data, indent=2))

        return len(dead)
```

- [ ] **Step 4: Run test, verify pass**

```bash
pytest tests/test_curation_pipeline.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add meme-trading/curation/pipeline.py meme-trading/tests/test_curation_pipeline.py
git commit -m "smc: prune dead auto-source wallets (no buys in N days)"
```

---

### Task 6: Rewrite `run_once` orchestration

**Files:**
- Modify: `meme-trading/curation/pipeline.py:50-68`
- Test: `meme-trading/tests/test_curation_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append to `meme-trading/tests/test_curation_pipeline.py`:

```python
@pytest.mark.asyncio
async def test_run_once_calls_gmgn_first_then_nansen_when_key_set(settings):
    """run_once: prune → GMGN → Nansen (only if key) → merge → sync."""
    settings.apify_api_token = "tok"
    settings.nansen_api_key = "nansen-tok"

    pipeline = CurationPipeline(settings)
    pipeline._prune_dead_wallets = AsyncMock(return_value=2)
    pipeline._discover_gmgn_apify = AsyncMock(return_value=[
        {"address": "G1", "score": 80, "stats": {"total_trades": 10, "win_rate": 60,
            "total_pnl_sol": 0, "avg_hold_minutes": 0}, "label_hint": "g", "source": "gmgn-apify"},
    ])
    pipeline._discover_nansen = AsyncMock(return_value=[
        {"address": "N1", "score": 72, "stats": {"total_trades": 5, "win_rate": 0,
            "total_pnl_sol": 0, "avg_hold_minutes": 0}, "label_hint": "n", "source": "nansen-live"},
    ])
    pipeline._merge_wallets = AsyncMock(return_value=(2, 0, 0))
    pipeline._sync_to_db = AsyncMock()

    await pipeline.run_once()

    pipeline._prune_dead_wallets.assert_awaited_once()
    pipeline._discover_gmgn_apify.assert_awaited_once()
    pipeline._discover_nansen.assert_awaited_once()
    # _merge_wallets called with both providers' candidates concatenated
    args, _ = pipeline._merge_wallets.call_args
    addrs = {w["address"] for w in args[0]}
    assert addrs == {"G1", "N1"}
    pipeline._sync_to_db.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_once_skips_nansen_when_no_key(settings):
    settings.apify_api_token = "tok"
    settings.nansen_api_key = ""

    pipeline = CurationPipeline(settings)
    pipeline._prune_dead_wallets = AsyncMock(return_value=0)
    pipeline._discover_gmgn_apify = AsyncMock(return_value=[
        {"address": "G1", "score": 80, "stats": {"total_trades": 10, "win_rate": 60,
            "total_pnl_sol": 0, "avg_hold_minutes": 0}, "label_hint": "g", "source": "gmgn-apify"},
    ])
    pipeline._discover_nansen = AsyncMock()
    pipeline._merge_wallets = AsyncMock(return_value=(1, 0, 0))
    pipeline._sync_to_db = AsyncMock()

    await pipeline.run_once()

    pipeline._discover_nansen.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_once_no_discoveries_still_syncs(settings):
    """If both providers return empty, still run prune + sync (no merge)."""
    settings.apify_api_token = ""
    settings.nansen_api_key = ""

    pipeline = CurationPipeline(settings)
    pipeline._prune_dead_wallets = AsyncMock(return_value=3)
    pipeline._discover_gmgn_apify = AsyncMock(return_value=[])
    pipeline._discover_nansen = AsyncMock(return_value=[])
    pipeline._merge_wallets = AsyncMock()
    pipeline._sync_to_db = AsyncMock()

    await pipeline.run_once()

    pipeline._merge_wallets.assert_not_awaited()
    pipeline._sync_to_db.assert_awaited_once()
```

- [ ] **Step 2: Run tests, verify failures**

```bash
pytest tests/test_curation_pipeline.py::test_run_once_calls_gmgn_first_then_nansen_when_key_set -v
```

Expected: FAIL — current `run_once` only calls Nansen.

- [ ] **Step 3: Replace `run_once`**

In `meme-trading/curation/pipeline.py`, replace the entire `run_once` method (lines 50–68 in current file):

```python
    async def run_once(self):
        """Single curation cycle: prune dead → discover GMGN → discover Nansen (opt) → merge → sync."""
        logger.info("Starting curation cycle...")

        pruned = await self._prune_dead_wallets(days=self.settings.wallet_prune_dead_days)

        gmgn_wallets = await self._discover_gmgn_apify()

        nansen_wallets: list[dict] = []
        if self.settings.nansen_api_key:
            nansen_wallets = await self._discover_nansen()

        new_wallets = gmgn_wallets + nansen_wallets

        added = updated = below_threshold = 0
        if new_wallets:
            added, updated, below_threshold = await self._merge_wallets(new_wallets)

        await self._sync_to_db()

        logger.info(
            "Curation cycle done: "
            f"gmgn={len(gmgn_wallets)} nansen={len(nansen_wallets)} "
            f"+{added} added, ~{updated} updated, "
            f"-{pruned} pruned-dead, -{below_threshold} below-threshold"
        )
```

- [ ] **Step 4: Run all tests, verify pass**

```bash
pytest tests/test_curation_pipeline.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Tag Nansen-discovered wallets with explicit `source`**

In the existing `_discover_nansen` method, the qualified-wallet dict (around line 119) does not set `"source"`. Update it so multi-source merge works correctly:

Locate this block in `_discover_nansen`:

```python
                qualified.append({
                    "address": addr,
                    "score": min(95, 70 + s["buys"]),
                    "stats": {
                        "total_trades": s["trades"],
                        "win_rate": 0,
                        "total_pnl_sol": 0,
                        "avg_hold_minutes": 0,
                    },
                    "label_hint": f"nansen-sm-{s['buys']}buys-{s['usd']:.0f}usd",
                    "tokens": tokens_str,
                })
```

Add `"source": "nansen-live"`:

```python
                qualified.append({
                    "address": addr,
                    "score": min(95, 70 + s["buys"]),
                    "stats": {
                        "total_trades": s["trades"],
                        "win_rate": 0,
                        "total_pnl_sol": 0,
                        "avg_hold_minutes": 0,
                    },
                    "label_hint": f"nansen-sm-{s['buys']}buys-{s['usd']:.0f}usd",
                    "tokens": tokens_str,
                    "source": "nansen-live",
                })
```

- [ ] **Step 6: Re-run all tests**

```bash
pytest tests/test_curation_pipeline.py -v
```

Expected: 9 PASS.

- [ ] **Step 7: Commit**

```bash
git add meme-trading/curation/pipeline.py meme-trading/tests/test_curation_pipeline.py
git commit -m "smc: rewrite run_once — GMGN primary, Nansen optional secondary, prune first"
```

---

### Task 7: Deploy to VPS

**Files:**
- Modify (VPS): `/docker/smc-trading/.env`
- Restart: `smc-trading` container

- [ ] **Step 1: Push committed changes to GitHub**

```bash
git push origin master
```

- [ ] **Step 2: Pull on VPS**

```bash
ssh root@46.202.146.30 "cd /docker/smc-trading && git pull origin master"
```

(If `/docker/smc-trading` is not a git checkout, use rsync or scp to deploy `meme-trading/curation/pipeline.py`, `meme-trading/config/settings.py`, and `meme-trading/requirements.txt` instead. Verify deploy method by running `ssh root@46.202.146.30 "cd /docker/smc-trading && git status"` first.)

- [ ] **Step 3: Update `.env` on VPS**

```bash
ssh root@46.202.146.30 "cd /docker/smc-trading && \
  sed -i 's/^SMC_CURATION_INTERVAL_HOURS=.*/SMC_CURATION_INTERVAL_HOURS=12/' .env || \
  echo 'SMC_CURATION_INTERVAL_HOURS=12' >> .env"

ssh root@46.202.146.30 "cd /docker/smc-trading && \
  sed -i 's/^SMC_MIN_CONVERGENCE_MINUTES=.*/SMC_MIN_CONVERGENCE_MINUTES=0.0/' .env || \
  echo 'SMC_MIN_CONVERGENCE_MINUTES=0.0' >> .env"

ssh root@46.202.146.30 "grep -E 'SMC_CURATION_INTERVAL_HOURS|SMC_MIN_CONVERGENCE_MINUTES|SMC_APIFY_API_TOKEN|SMC_NANSEN_API_KEY' /docker/smc-trading/.env"
```

Expected output: shows new values, plus the existing Apify token and (just-rotated) Nansen key.

- [ ] **Step 4: Rebuild + restart container**

```bash
ssh root@46.202.146.30 "cd /docker/smc-trading && docker compose up -d --build"
```

Expected: container rebuilds and comes up healthy.

- [ ] **Step 5: Verify startup logs reflect new config**

```bash
ssh root@46.202.146.30 "docker logs smc-trading --tail 50 2>&1 | grep -E 'Convergence speed|Curation pipeline started'"
```

Expected: `Convergence speed: 0.0-20.0min` and `Curation pipeline started (interval: 12.0h)`.

- [ ] **Step 6: Watch for first cycle (or trigger early)**

Wait up to 30 seconds (the `await asyncio.sleep(30)` in `run_loop`), then:

```bash
ssh root@46.202.146.30 "docker logs smc-trading --since 2m 2>&1 | grep -E 'Curation cycle|GMGN-Apify|qualified'"
```

Expected: lines like `GMGN-Apify smart_degen: N raw`, `GMGN-Apify discovery: N unique candidates → M qualified`, `Curation cycle done: gmgn=M nansen=K +X added, ...`.

- [ ] **Step 7: Confirm new GMGN wallets landed**

```bash
ssh root@46.202.146.30 "sqlite3 /var/lib/docker/volumes/smc-trading_smc-data/_data/smc.db \
  \"SELECT source, COUNT(*) FROM tracked_wallets WHERE active=1 GROUP BY source;\""
```

Expected: `gmgn-apify` count higher than 48; `nansen-live` count significantly lower (most pruned).

---

### Task 8: Memory housekeeping

**Files:**
- Modify: `~/.claude/projects/C--Users-rakai-Leverage/memory/project_runner_cook_mode.md`

The cook-mode memory says "no tuning until ≥30 closed paper positions." We're at 103 closed with a clear losing verdict, and we just acted on it. Memory must reflect that.

- [ ] **Step 1: Read current cook-mode memory**

```bash
cat ~/.claude/projects/C--Users-rakai-Leverage/memory/project_runner_cook_mode.md
```

- [ ] **Step 2: Replace its body**

Update the file's body so it reads:

```markdown
**Status (2026-04-27):** Cook window CLOSED. 103 closed paper positions, net −1.044 SOL (avg −14.17%, 18% win rate). Verdict: original config loses money.

**Action taken (2026-04-27):** Switched primary wallet discovery from Nansen → GMGN-Apify (per-capita 20× more productive). Nansen kept as optional secondary. Convergence-speed floor dropped from 10min → 0min so fast-conviction signals can finally trade. See plan: `Leverage/docs/superpowers/plans/2026-04-27-gmgn-primary-discovery.md`.

**Why:** Cook gathered evidence; evidence pointed to losing config; Rich asked us to fix root issues, not preserve a stale "don't touch" rule.

**How to apply:** Cook-mode rule is no longer active. Tuning and feature work on smc-trading is unblocked. New baseline is the post-2026-04-27 config; if results are evaluated again, compare against this checkpoint.
```

Keep the existing frontmatter block (`---`...`---`) intact. Update the `description:` field to reflect the new state — e.g. `description: smc-trading cook window closed 2026-04-27 — config losing, swapped Nansen→GMGN, tuning unblocked`.

- [ ] **Step 3: Update MEMORY.md index entry**

Edit `~/.claude/projects/C--Users-rakai-Leverage/memory/MEMORY.md` — find the `Runner Cook Mode` line and replace its hook with: `— Cook window CLOSED 2026-04-27. Verdict: losing config, acted on it (GMGN swap). Tuning unblocked.`

---

## Self-review checklist (executed by plan author)

**Spec coverage:**
- [x] GMGN as primary discovery → Tasks 4, 6
- [x] Nansen as optional secondary → Task 6 (`if settings.nansen_api_key`)
- [x] Cadence 6h → 12h → Task 1
- [x] Prune dead-weight wallets → Task 5
- [x] Cap new wallets per cycle (Rich: "we don't need to just keep jamming") → Task 1 (`gmgn_max_new_per_cycle=20`) + Task 4
- [x] Convergence speed filter unblocked → Task 1 (`min=0.0`)
- [x] Healthcheck-style cycle log → Task 6 (`Curation cycle done: ...`)
- [x] Tests for new pipeline path → Tasks 3, 4, 5, 6
- [x] VPS deploy → Task 7
- [x] Memory housekeeping → Task 8

**Placeholders:** none. All steps include exact code/commands.

**Type consistency:** `_discover_gmgn_apify`, `_discover_nansen`, `_prune_dead_wallets`, `_merge_wallets`, `_sync_to_db` — all used consistently. Candidate dict shape (`address`, `score`, `stats`, `label_hint`, `source`) is identical across both discovery methods and what `_merge_wallets` consumes.

**Out of scope (not part of this plan):**
- Changing `convergence_threshold` or `convergence_window_minutes` — those are separate tuning levers, evaluate after this lands.
- Any change to safety checks, position sizing, or exit policy.
- Live mode flip — still paper.
