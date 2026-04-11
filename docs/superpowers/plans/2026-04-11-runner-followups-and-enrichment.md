# Runner Plan 2a — Plan 1 Followups + Enrichment Layer

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the must-fix items from the Plan 1 capstone review (wallet tier bootstrap, event persistence, main.py hygiene, hot-reload for detector) and build the enrichment layer that turns a `ClusterSignal` into a fully-loaded `EnrichedToken` ready for filtering and scoring.

**Architecture:** A new `runner/enrichment/` package with three independent fetchers (Helius DAS metadata, DexScreener+Jupiter price/liquidity, Helius deployer history), orchestrated by an `Enricher` that fans out to all three via `asyncio.gather` and assembles an immutable `EnrichedToken` dataclass. Persistence for `buy_events` and `cluster_signals` lands in the existing ingest/cluster modules. The `_drain` Phase 3 sink is replaced by the enricher and a new `enriched_token_bus`.

**Tech Stack:**
- Python 3.11+, asyncio
- `aiosqlite` for persistence
- `httpx` via the existing `RateLimitedClient` for all external calls
- `pytest`, `pytest-asyncio`, `respx` (httpx mocking)

**Reference spec:** `docs/superpowers/specs/2026-04-11-meme-runner-design.md`
**Preceding plan:** `docs/superpowers/plans/2026-04-11-runner-foundation-ingest-cluster.md` (Plan 1, complete)

**Parent folder:** All file paths below are relative to `meme-trading/runner/` unless stated otherwise.

---

## File Structure

**New files:**

```
meme-trading/runner/
├── scripts/
│   ├── __init__.py                     # NEW
│   └── bootstrap_wallet_tiers.py       # NEW — seed runner.db wallet_tiers from wallets.json
│
├── enrichment/                          # NEW package
│   ├── __init__.py
│   ├── schemas.py                       # EnrichedToken dataclass
│   ├── token_metadata.py                # Helius DAS getAsset client
│   ├── price_liquidity.py               # DexScreener + Jupiter price/slippage
│   ├── deployer.py                      # Helius deployer history
│   └── enricher.py                      # Orchestrator
│
└── tests/
    ├── unit/
    │   ├── test_bootstrap_wallet_tiers.py      # NEW
    │   ├── test_buy_events_persistence.py      # NEW (wallet_monitor extension)
    │   ├── test_cluster_signals_persistence.py # NEW (convergence extension)
    │   ├── test_enriched_token.py              # NEW
    │   ├── test_token_metadata.py              # NEW
    │   ├── test_price_liquidity.py             # NEW
    │   ├── test_deployer.py                    # NEW
    │   └── test_enricher.py                    # NEW
    ├── integration/
    │   └── test_ingest_cluster_enrichment.py   # NEW — e2e through enrichment
    └── fixtures/
        ├── das_getAsset_fungible.json          # NEW
        ├── dexscreener_pairs.json              # NEW
        ├── jupiter_price.json                  # NEW
        ├── jupiter_quote_buy.json              # NEW
        ├── jupiter_quote_sell.json             # NEW
        └── helius_signatures_deployer.json     # NEW
```

**Files to modify:**

```
meme-trading/runner/
├── ingest/wallet_monitor.py     # Add db persistence for BuyEvents
├── cluster/convergence.py       # Add db persistence for ClusterSignals; hot-reload weights
├── main.py                      # Hygiene: explicit per_host_rps, _drain try/except, supervisor, wire enricher
└── config/weights.yaml          # No changes required (already has cluster section)
```

---

## Task 1: Bootstrap wallet tiers script

**Why:** Plan 1 capstone review found that `wallet_tiers` is empty on a fresh DB, so every wallet falls through to `Tier.U`, and `ConvergenceDetector` only counts A+B — the system is a silent no-op. This task ships a one-shot bootstrap that marks every active wallet in `config/wallets.json` as Tier A with `source="manual_bootstrap"`. Operators run it once before the first `main.py` start.

**Files:**
- Create: `scripts/__init__.py` (empty)
- Create: `scripts/bootstrap_wallet_tiers.py`
- Create: `tests/unit/test_bootstrap_wallet_tiers.py`

- [ ] **Step 1: Create empty scripts/__init__.py**

`scripts/__init__.py`:

```python
```

(Empty file — makes `scripts` a package so `python -m runner.scripts.bootstrap_wallet_tiers` works.)

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_bootstrap_wallet_tiers.py`:

```python
"""Bootstrap script seeds wallet_tiers from wallets.json as Tier A."""
import json
from pathlib import Path

import pytest

from runner.db.database import Database
from runner.scripts.bootstrap_wallet_tiers import bootstrap_wallet_tiers


@pytest.fixture
def wallets_json(tmp_path: Path) -> Path:
    p = tmp_path / "wallets.json"
    p.write_text(
        json.dumps(
            {
                "wallets": [
                    {
                        "address": "W1",
                        "name": "active-1",
                        "source": "nansen",
                        "active": True,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "address": "W2",
                        "name": "active-2",
                        "source": "gmgn",
                        "active": True,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "address": "W3",
                        "name": "inactive",
                        "source": "manual",
                        "active": False,
                        "added_at": "2026-01-01T00:00:00Z",
                    },
                ]
            }
        )
    )
    return p


@pytest.mark.asyncio
async def test_bootstrap_inserts_active_wallets_as_tier_a(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    count = await bootstrap_wallet_tiers(db, wallets_json)

    assert count == 2

    async with db.conn.execute(
        "SELECT wallet_address, tier, source FROM wallet_tiers ORDER BY wallet_address"
    ) as cur:
        rows = await cur.fetchall()

    assert rows == [
        ("W1", "A", "manual_bootstrap"),
        ("W2", "A", "manual_bootstrap"),
    ]
    await db.close()


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    await bootstrap_wallet_tiers(db, wallets_json)
    # Second run should not duplicate rows
    count = await bootstrap_wallet_tiers(db, wallets_json)
    assert count == 2

    async with db.conn.execute("SELECT COUNT(*) FROM wallet_tiers") as cur:
        total = (await cur.fetchone())[0]
    assert total == 2

    await db.close()


@pytest.mark.asyncio
async def test_bootstrap_does_not_downgrade_existing_non_u_tiers(tmp_path, wallets_json):
    db = Database(tmp_path / "r.db")
    await db.connect()

    # Pre-seed W1 as Tier B
    await db.conn.execute(
        "INSERT INTO wallet_tiers (wallet_address, tier, source) VALUES ('W1', 'B', 'rebuilder')"
    )
    await db.conn.commit()

    await bootstrap_wallet_tiers(db, wallets_json)

    async with db.conn.execute(
        "SELECT tier, source FROM wallet_tiers WHERE wallet_address = 'W1'"
    ) as cur:
        row = await cur.fetchone()
    # W1 should still be B — bootstrap only fills in missing
    assert row == ("B", "rebuilder")

    await db.close()
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/unit/test_bootstrap_wallet_tiers.py -v
```

Expected: `ModuleNotFoundError: No module named 'runner.scripts.bootstrap_wallet_tiers'`.

- [ ] **Step 4: Implement the bootstrap script**

Create `scripts/bootstrap_wallet_tiers.py`:

```python
"""Bootstrap script: seed wallet_tiers from wallets.json as Tier A.

Idempotent — only inserts wallets that don't already have a tier row.
Run once before the first main.py start, or whenever new wallets are added.

Usage:
    python -m runner.scripts.bootstrap_wallet_tiers
"""
import asyncio
import json
import sys
from pathlib import Path

from runner.db.database import Database
from runner.utils.logging import configure_logging, get_logger

logger = get_logger("runner.scripts.bootstrap_wallet_tiers")


async def bootstrap_wallet_tiers(db: Database, wallets_json_path: Path | str) -> int:
    """Insert every active wallet from wallets.json into wallet_tiers as Tier A.

    Returns the number of active wallets processed (not necessarily inserted —
    existing rows are skipped).
    """
    wallets_path = Path(wallets_json_path)
    if not wallets_path.exists():
        raise FileNotFoundError(f"wallets file not found: {wallets_path}")

    data = json.loads(wallets_path.read_text(encoding="utf-8"))
    wallets = data.get("wallets") or []
    active = [w for w in wallets if w.get("active") and "address" in w]

    assert db.conn is not None
    inserted = 0
    for w in active:
        result = await db.conn.execute(
            """
            INSERT INTO wallet_tiers (wallet_address, tier, source)
            VALUES (?, 'A', 'manual_bootstrap')
            ON CONFLICT(wallet_address) DO NOTHING
            """,
            (w["address"],),
        )
        if result.rowcount:
            inserted += 1
    await db.conn.commit()

    logger.info(
        "bootstrap_complete",
        active_wallets=len(active),
        newly_inserted=inserted,
    )
    return len(active)


async def _main() -> None:
    configure_logging("INFO")
    # Defer settings import so the script can be tested without env vars
    from runner.config.settings import get_settings

    settings = get_settings()
    db = Database(settings.db_path)
    await db.connect()
    try:
        count = await bootstrap_wallet_tiers(db, settings.wallets_json_path)
        print(f"Bootstrapped {count} active wallets.")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
    sys.exit(0)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/unit/test_bootstrap_wallet_tiers.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/scripts/ meme-trading/runner/tests/unit/test_bootstrap_wallet_tiers.py
git commit -m "runner: bootstrap wallet tiers script to seed Tier A from wallets.json"
git push
```

---

## Task 2: Persist BuyEvents in WalletMonitor

**Why:** Plan 1 creates `BuyEvent` and puts them on the queue, but `buy_events` table stays empty. If the process dies, in-flight window state vaporizes. Adding a single insert after the queue put gives us crash recovery for future sessions and an audit log for debugging.

**Files:**
- Modify: `ingest/wallet_monitor.py` (add `db` parameter and insert)
- Create: `tests/unit/test_buy_events_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_buy_events_persistence.py`:

```python
"""WalletMonitor persists BuyEvents to the buy_events table."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from runner.db.database import Database
from runner.ingest.events import BuyEvent
from runner.ingest.wallet_monitor import WalletMonitor


@pytest.mark.asyncio
async def test_persists_emitted_buy_event(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigP1",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.5,
        token_amount=1000.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=db,
    )
    await monitor.handle_signature("sigP1", "W")

    async with db.conn.execute(
        "SELECT signature, wallet_address, token_mint, sol_amount, token_amount, price_sol FROM buy_events"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0] == ("sigP1", "W", "MINT1", 0.5, 1000.0, 0.0005)

    await db.close()


@pytest.mark.asyncio
async def test_duplicate_signature_does_not_double_insert(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigDup",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.25,
        token_amount=500.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=db,
    )
    await monitor.handle_signature("sigDup", "W")
    await monitor.handle_signature("sigDup", "W")

    async with db.conn.execute("SELECT COUNT(*) FROM buy_events") as cur:
        count = (await cur.fetchone())[0]
    assert count == 1

    await db.close()


@pytest.mark.asyncio
async def test_monitor_without_db_still_works(tmp_path):
    """Passing db=None preserves the existing test-mode behavior."""
    bus: asyncio.Queue = asyncio.Queue()
    parser = AsyncMock()
    parser.parse_transaction.return_value = BuyEvent(
        signature="sigN",
        wallet_address="W",
        token_mint="MINT1",
        sol_amount=0.25,
        token_amount=500.0,
        price_sol=0.0005,
        block_time=datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc),
    )

    monitor = WalletMonitor(
        wallets={"W": {"tier": "A"}},
        event_bus=bus,
        parser=parser,
        db=None,
    )
    await monitor.handle_signature("sigN", "W")
    assert bus.qsize() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/unit/test_buy_events_persistence.py -v
```

Expected: `TypeError: WalletMonitor.__init__() got an unexpected keyword argument 'db'`.

- [ ] **Step 3: Modify WalletMonitor to accept db and persist**

Edit `ingest/wallet_monitor.py`. Add `Database` import, accept optional `db` in `__init__`, and insert after the `event_bus.put` call.

Change the imports block:

```python
import asyncio
import json
from collections import OrderedDict

import websockets

from runner.db.database import Database
from runner.ingest.transaction_parser import TransactionParser
from runner.utils.logging import get_logger
```

Change `__init__` to:

```python
    def __init__(
        self,
        wallets: dict[str, dict],
        event_bus: asyncio.Queue,
        parser: TransactionParser,
        ws_url: str = "",
        max_seen: int = 10000,
        db: Database | None = None,
    ):
        self.wallets = wallets
        self.event_bus = event_bus
        self.parser = parser
        self.ws_url = ws_url
        self._seen_signatures: OrderedDict[str, None] = OrderedDict()
        self._max_seen = max_seen
        self.db = db
        self._running = True
```

Change `handle_signature` to insert after emission. Replace the emission block at the end with:

```python
        event = await self.parser.parse_transaction(signature, wallet_address)
        if event is None:
            return
        await self.event_bus.put(event)
        if self.db is not None and self.db.conn is not None:
            try:
                await self.db.conn.execute(
                    """
                    INSERT OR IGNORE INTO buy_events
                    (signature, wallet_address, token_mint, sol_amount,
                     token_amount, price_sol, block_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    event.to_db_row(),
                )
                await self.db.conn.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "buy_event_persist_failed",
                    signature=event.signature,
                    error=str(e),
                )
        logger.info(
            "buy_event",
            signature=event.signature,
            wallet=event.wallet_address,
            mint=event.token_mint,
            sol=event.sol_amount,
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_buy_events_persistence.py tests/unit/test_wallet_monitor.py -v
```

Expected: 3 new tests pass + all 5 original `test_wallet_monitor` tests still pass (8 total).

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 61 passed (55 Plan 1 + 3 Task 1 + 3 Task 2).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/ingest/wallet_monitor.py meme-trading/runner/tests/unit/test_buy_events_persistence.py
git commit -m "runner: persist BuyEvents to buy_events table from WalletMonitor"
git push
```

---

## Task 3: Persist ClusterSignals in ConvergenceDetector

**Why:** Same rationale as Task 2 — `cluster_signals` table exists but is never written. Persisting makes replay and debugging possible.

**Files:**
- Modify: `cluster/convergence.py`
- Create: `tests/unit/test_cluster_signals_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cluster_signals_persistence.py`:

```python
"""ConvergenceDetector persists ClusterSignals to the cluster_signals table."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping: dict[str, Tier]):
        self._map = mapping

    async def load(self):
        pass


def _ev(sig, wallet, mint, t):
    return BuyEvent(
        signature=sig,
        wallet_address=wallet,
        token_mint=mint,
        sol_amount=0.25,
        token_amount=1000,
        price_sol=0.00025,
        block_time=t,
    )


@pytest.mark.asyncio
async def test_persists_cluster_signal(tmp_path):
    db = Database(tmp_path / "r.db")
    await db.connect()

    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
        db=db,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # Drain the signal from the bus (the test shouldn't leak it)
    sig_bus.get_nowait()

    async with db.conn.execute(
        "SELECT token_mint, wallet_count, wallets_json, tier_counts_json, "
        "convergence_seconds, mid_price_sol FROM cluster_signals"
    ) as cur:
        rows = await cur.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == "TOKEN"
    assert row[1] == 3
    assert sorted(json.loads(row[2])) == ["A1", "A2", "B1"]
    assert json.loads(row[3]) == {"A": 2, "B": 1}
    assert row[4] == 600  # 10 minutes
    assert row[5] == pytest.approx(0.00025)

    await db.close()


@pytest.mark.asyncio
async def test_detector_without_db_still_works():
    """db=None preserves existing Plan 1 unit-test behavior."""
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()
    tier_cache = _StubTierCache({"A1": Tier.A, "A2": Tier.A, "B1": Tier.B})

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        min_wallets=3,
        window_minutes=30,
        db=None,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    assert sig_bus.qsize() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_cluster_signals_persistence.py -v
```

Expected: `TypeError: ConvergenceDetector.__init__() got an unexpected keyword argument 'db'`.

- [ ] **Step 3: Modify ConvergenceDetector to accept db and persist**

Edit `cluster/convergence.py`. Add `Database` import and `json`.

Update imports:

```python
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean

from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.db.database import Database
from runner.ingest.events import BuyEvent
from runner.utils.logging import get_logger
```

Update `__init__`:

```python
    def __init__(
        self,
        event_bus: asyncio.Queue,
        signal_bus: asyncio.Queue,
        tier_cache: WalletTierCache,
        min_wallets: int = 3,
        window_minutes: int = 30,
        db: Database | None = None,
    ):
        self.event_bus = event_bus
        self.signal_bus = signal_bus
        self.tier_cache = tier_cache
        self.min_wallets = min_wallets
        self.window_minutes = window_minutes
        self.db = db
        self._window: dict[str, list[BuyEvent]] = defaultdict(list)
        self._signaled: dict[str, set[frozenset[str]]] = defaultdict(set)
```

At the end of `_process`, before the `await self.signal_bus.put(signal)` line, add DB insert:

```python
        if self.db is not None and self.db.conn is not None:
            try:
                await self.db.conn.execute(
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
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "cluster_signal_persist_failed",
                    mint=signal.token_mint,
                    error=str(e),
                )
        logger.info(
            "cluster_signal",
            mint=token,
            wallets=signal.wallet_count,
            tier_counts=signal.tier_counts,
            convergence_seconds=signal.convergence_seconds,
        )
        await self.signal_bus.put(signal)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_cluster_signals_persistence.py tests/unit/test_convergence.py -v
```

Expected: 2 new tests pass + 6 original convergence tests still pass (8 total).

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: 63 passed (61 prior + 2 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/cluster/convergence.py meme-trading/runner/tests/unit/test_cluster_signals_persistence.py
git commit -m "runner: persist ClusterSignals to cluster_signals table"
git push
```

---

## Task 4: Hot-reload weights in ConvergenceDetector

**Why:** Plan 1 capstone found that `ConvergenceDetector` captures `min_wallets` and `window_minutes` at construction, so editing `weights.yaml` at runtime doesn't propagate. We promised hot-reloadability; this delivers it for the detector (filters and scorer in Plan 2b will inherit the pattern).

**Approach:** Instead of storing the values, store a `WeightsLoader` reference and read via `loader.get(...)` at the top of each `_process()` call. Call `loader.check_and_reload()` on every event so mtime-based reload fires without a separate watchdog task.

**Files:**
- Modify: `cluster/convergence.py`
- Modify: `tests/unit/test_convergence.py` (update constructor calls — accept a loader or keep explicit ints)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_convergence.py`:

```python
@pytest.mark.asyncio
async def test_picks_up_weights_changes_at_runtime(tmp_path):
    """Editing weights.yaml during runtime changes detection thresholds."""
    from runner.config.weights_loader import WeightsLoader

    yaml_file = tmp_path / "weights.yaml"
    yaml_file.write_text(
        """
cluster:
  min_wallets: 4
  window_minutes: 30
"""
    )
    loader = WeightsLoader(yaml_file)

    tier_cache = _StubTierCache(
        {"A1": Tier.A, "A2": Tier.A, "B1": Tier.B}
    )
    ev_bus: asyncio.Queue = asyncio.Queue()
    sig_bus: asyncio.Queue = asyncio.Queue()

    det = ConvergenceDetector(
        event_bus=ev_bus,
        signal_bus=sig_bus,
        tier_cache=tier_cache,
        weights=loader,
    )

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    await det._process(_ev("s1", "A1", "TOKEN", base))
    await det._process(_ev("s2", "A2", "TOKEN", base + timedelta(minutes=5)))
    await det._process(_ev("s3", "B1", "TOKEN", base + timedelta(minutes=10)))

    # With min_wallets=4, 3 wallets should NOT fire a signal
    assert sig_bus.empty()

    # Now lower the threshold by editing the YAML
    import time
    time.sleep(0.01)
    yaml_file.write_text(
        """
cluster:
  min_wallets: 3
  window_minutes: 30
"""
    )
    yaml_file.touch()

    # Next event triggers reload on check; now 3 wallets should fire
    await det._process(_ev("s4", "A1", "TOKEN2", base))
    await det._process(_ev("s5", "A2", "TOKEN2", base + timedelta(minutes=5)))
    await det._process(_ev("s6", "B1", "TOKEN2", base + timedelta(minutes=10)))

    signal = sig_bus.get_nowait()
    assert signal.token_mint == "TOKEN2"
    assert signal.wallet_count == 3
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
python -m pytest tests/unit/test_convergence.py::test_picks_up_weights_changes_at_runtime -v
```

Expected: `TypeError: ConvergenceDetector.__init__() got an unexpected keyword argument 'weights'`.

- [ ] **Step 3: Change ConvergenceDetector to accept either weights or explicit ints**

Edit `cluster/convergence.py`. Make `weights` an optional parameter; if provided, read from it at runtime. If not, keep the existing `min_wallets` / `window_minutes` for backward-compatibility.

Update imports:

```python
from runner.config.weights_loader import WeightsLoader
```

Update `__init__`:

```python
    def __init__(
        self,
        event_bus: asyncio.Queue,
        signal_bus: asyncio.Queue,
        tier_cache: WalletTierCache,
        min_wallets: int = 3,
        window_minutes: int = 30,
        db: Database | None = None,
        weights: WeightsLoader | None = None,
    ):
        self.event_bus = event_bus
        self.signal_bus = signal_bus
        self.tier_cache = tier_cache
        self._static_min_wallets = min_wallets
        self._static_window_minutes = window_minutes
        self.db = db
        self.weights = weights
        self._window: dict[str, list[BuyEvent]] = defaultdict(list)
        self._signaled: dict[str, set[frozenset[str]]] = defaultdict(set)
```

Add two property methods:

```python
    @property
    def min_wallets(self) -> int:
        if self.weights is not None:
            return int(self.weights.get("cluster.min_wallets", self._static_min_wallets))
        return self._static_min_wallets

    @property
    def window_minutes(self) -> int:
        if self.weights is not None:
            return int(self.weights.get("cluster.window_minutes", self._static_window_minutes))
        return self._static_window_minutes
```

At the top of `_process()`, before any logic, add a reload check:

```python
    async def _process(self, event: BuyEvent) -> None:
        if self.weights is not None:
            self.weights.check_and_reload()
        # ... existing logic
```

- [ ] **Step 4: Run the new test — expect PASS**

```bash
python -m pytest tests/unit/test_convergence.py::test_picks_up_weights_changes_at_runtime -v
```

- [ ] **Step 5: Run all convergence tests + full suite**

```bash
python -m pytest tests/unit/test_convergence.py tests/ -v
```

Expected: 64 passed (63 prior + 1 new).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/cluster/convergence.py meme-trading/runner/tests/unit/test_convergence.py
git commit -m "runner: convergence detector reads cluster thresholds from WeightsLoader for hot-reload"
git push
```

---

## Task 5: main.py hygiene (explicit per_host_rps, drain try/except, gather supervisor)

**Why:** Three carry-forward items from Plan 1 capstone:
1. `per_host_rps={}` is implicit — if a second host is added later, it silently falls through to `default_rps`. Fix by passing the Helius host explicitly.
2. `_drain` has no exception handling — a bad signal kills the process.
3. `asyncio.gather(...)` without `return_exceptions=True` — if one task dies, all siblings get cancelled silently.

**Files:**
- Modify: `main.py`

This task has no new tests — it's runtime wiring that the integration test in Task 10 will exercise. We verify with the full suite + import smoke check.

- [ ] **Step 1: Update main.py**

Replace the body of `_main()` in `main.py`. Keep the top imports and the outer signature. Replace everything after `logger.info("starting", ...)` with:

```python
async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger("runner.main")
    logger.info("starting", log_level=settings.log_level)

    db = Database(settings.db_path)
    await db.connect()

    weights = WeightsLoader(settings.weights_yaml_path)

    registry = WalletRegistry(settings.wallets_json_path)
    registry.load()

    tier_cache = WalletTierCache(db)
    await tier_cache.load()

    # Compute helius host for explicit per-host rate limit binding
    from urllib.parse import urlparse

    helius_host = urlparse(settings.helius_rpc_url).netloc.lower()
    helius_rps = weights.get("http_rate_limits.helius_rps", 10)

    http = RateLimitedClient(
        default_rps=helius_rps,
        per_host_rps={helius_host: helius_rps} if helius_host else {},
        timeout=15.0,
    )

    rpc_pool = RpcPool([settings.helius_rpc_url])
    parser = TransactionParser(rpc_pool, http)

    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()

    active = registry.active_addresses()
    wallets_map = {addr: registry.get(addr) for addr in active}

    monitor = WalletMonitor(
        wallets=wallets_map,
        event_bus=event_bus,
        parser=parser,
        ws_url=settings.helius_ws_url,
        db=db,
    )

    detector = ConvergenceDetector(
        event_bus=event_bus,
        signal_bus=signal_bus,
        tier_cache=tier_cache,
        db=db,
        weights=weights,
    )

    logger.info(
        "wired",
        active_wallets=len(active),
        cluster_min=weights.get("cluster.min_wallets"),
        cluster_window=weights.get("cluster.window_minutes"),
        helius_host=helius_host,
        helius_rps=helius_rps,
    )

    try:
        results = await asyncio.gather(
            _supervise(monitor.run, "wallet_monitor", logger),
            _supervise(detector.run, "convergence_detector", logger),
            _supervise(lambda: _drain(signal_bus, logger), "drain", logger),
            return_exceptions=True,
        )
        for name, result in zip(["monitor", "detector", "drain"], results):
            if isinstance(result, Exception):
                logger.error("task_exited_with_exception", task=name, error=str(result))
    finally:
        await http.aclose()
        await db.close()


async def _supervise(factory, name: str, logger) -> None:
    """Run a long-lived task forever, restarting it on unexpected exceptions.

    `factory` is a zero-arg callable that returns the coroutine to await.
    Most tasks we run here are infinite loops — if one exits via raise,
    we log and restart with an exponential backoff cap.
    """
    backoff = 1.0
    while True:
        try:
            await factory()
            # Factory returned cleanly — treat as an intentional exit.
            logger.info("task_exit_clean", task=name)
            return
        except asyncio.CancelledError:
            logger.info("task_cancelled", task=name)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(
                "task_crashed_restarting",
                task=name,
                error=str(e),
                backoff=backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 60.0)


async def _drain(signal_bus: asyncio.Queue, logger) -> None:
    """Phase 3 sink: log every signal. Replaced by Enricher in Task 10.

    Wrapped in per-iteration try/except so a bad signal can't kill the process.
    """
    while True:
        try:
            signal = await signal_bus.get()
            logger.info(
                "signal_drained",
                mint=signal.token_mint,
                wallets=signal.wallet_count,
                tier_counts=signal.tier_counts,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_iteration_error", error=str(e))
```

- [ ] **Step 2: Run full suite**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/ -v
```

Expected: 64 passed (no new tests, but nothing regresses).

- [ ] **Step 3: Verify main.py imports**

```bash
cd /c/Users/rakai/Leverage/meme-trading
python -c "import sys; sys.path.insert(0, '.'); from runner.main import _main, _drain, _supervise; print('main ok')"
```

Expected: prints `main ok`.

- [ ] **Step 4: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/main.py
git commit -m "runner: main.py hygiene — explicit per_host_rps, drain try/except, task supervisor"
git push
```

---

## Task 6: EnrichedToken dataclass

**Why:** The contract between the cluster engine, enrichment, filters, and scoring is a single immutable dataclass carrying everything a candidate knows about itself. Creating it first locks the interface so the fetchers (Tasks 7-9) can target real fields.

**Files:**
- Create: `enrichment/__init__.py` (empty)
- Create: `enrichment/schemas.py`
- Create: `tests/unit/test_enriched_token.py`

- [ ] **Step 1: Create empty enrichment/__init__.py**

```python
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_enriched_token.py`:

```python
"""EnrichedToken dataclass carries everything a candidate knows about itself."""
from datetime import datetime, timedelta, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken


def _sig() -> ClusterSignal:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint="MINT",
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )


def test_can_construct_with_full_fields():
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        symbol="WIFHAT",
        name="WIF Hat",
        decimals=6,
        supply=1_000_000_000.0,
        token_created_at=datetime(2026, 4, 11, 9, 30, tzinfo=timezone.utc),
        price_sol=0.00026,
        price_usd=0.0001,
        liquidity_usd=42000.0,
        volume_24h_usd=150000.0,
        pair_age_seconds=1800,
        slippage_at_size_pct={0.25: 1.2, 0.5: 2.8},
        deployer_address="Deployer1",
        deployer_age_seconds=3600 * 24 * 7,
        deployer_token_count=3,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
        errors=[],
    )

    assert enriched.token_mint == "MINT"
    assert enriched.cluster_signal.wallet_count == 3
    assert enriched.symbol == "WIFHAT"
    assert enriched.slippage_at_size_pct[0.25] == 1.2
    assert enriched.errors == []


def test_optional_fields_default_to_none():
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
    )

    assert enriched.symbol is None
    assert enriched.name is None
    assert enriched.price_sol is None
    assert enriched.deployer_address is None
    assert enriched.slippage_at_size_pct == {}
    assert enriched.errors == []


def test_is_frozen():
    import dataclasses
    sig = _sig()
    enriched = EnrichedToken(
        token_mint="MINT",
        cluster_signal=sig,
        enriched_at=datetime(2026, 4, 11, 10, 11, tzinfo=timezone.utc),
    )
    try:
        enriched.symbol = "NEW"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("EnrichedToken must be frozen")
```

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/unit/test_enriched_token.py -v
```

Expected: `ModuleNotFoundError: No module named 'runner.enrichment'`.

- [ ] **Step 4: Implement schemas.py**

Create `enrichment/schemas.py`:

```python
"""EnrichedToken dataclass — the unit the filter/scoring pipeline consumes."""
from dataclasses import dataclass, field
from datetime import datetime

from runner.cluster.convergence import ClusterSignal


@dataclass(frozen=True)
class EnrichedToken:
    """A cluster signal expanded with metadata, price, liquidity, and deployer info.

    Required fields are the minimum a candidate must have to flow through
    downstream filters; optional fields are populated by the enrichment
    sub-fetchers when they succeed. Each sub-fetcher failure adds an entry
    to `errors` instead of raising, so one slow/broken API can't sink a
    candidate.
    """

    token_mint: str
    cluster_signal: ClusterSignal
    enriched_at: datetime

    # Metadata (Helius DAS)
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = None
    supply: float | None = None
    token_created_at: datetime | None = None

    # Price / liquidity (DexScreener + Jupiter)
    price_sol: float | None = None
    price_usd: float | None = None
    liquidity_usd: float | None = None
    volume_24h_usd: float | None = None
    pair_age_seconds: int | None = None
    slippage_at_size_pct: dict[float, float] = field(default_factory=dict)

    # Deployer history (Helius)
    deployer_address: str | None = None
    deployer_age_seconds: int | None = None
    deployer_token_count: int | None = None

    # Per-fetcher failures, collected non-fatally
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/unit/test_enriched_token.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/enrichment/__init__.py meme-trading/runner/enrichment/schemas.py meme-trading/runner/tests/unit/test_enriched_token.py
git commit -m "runner: EnrichedToken dataclass for enrichment pipeline"
git push
```

---

## Task 7: Token metadata fetcher (Helius DAS getAsset)

**Why:** First of the three enrichment fetchers. Uses Helius DAS `getAsset` to pull symbol, name, decimals, supply, and creation time for a token mint. DAS returns these via the Metaplex metadata interface.

**Files:**
- Create: `enrichment/token_metadata.py`
- Create: `tests/fixtures/das_getAsset_fungible.json`
- Create: `tests/unit/test_token_metadata.py`

- [ ] **Step 1: Create the fixture**

Create `tests/fixtures/das_getAsset_fungible.json`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "interface": "FungibleToken",
    "id": "TestMint1111111111111111111111111111111111",
    "content": {
      "$schema": "https://schema.metaplex.com/nft1.0.json",
      "metadata": {
        "name": "WIF Hat",
        "symbol": "WIFHAT",
        "description": "A test memecoin"
      }
    },
    "token_info": {
      "decimals": 6,
      "supply": 1000000000000000,
      "mint_authority": null,
      "freeze_authority": null,
      "price_info": {
        "price_per_token": 0.0001,
        "currency": "USDC"
      }
    },
    "mint_extensions": {},
    "ownership": {
      "owner": "",
      "frozen": false
    },
    "royalty": null,
    "creators": [],
    "compression": null
  }
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_token_metadata.py`:

```python
"""Helius DAS getAsset metadata fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.token_metadata import TokenMetadataFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "das_getAsset_fungible.json"


@pytest.mark.asyncio
async def test_fetch_parses_metadata_from_recorded_response():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    payload = json.loads(FIX.read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=payload))
        meta = await fetcher.fetch("TestMint1111111111111111111111111111111111")

    assert meta is not None
    assert meta["symbol"] == "WIFHAT"
    assert meta["name"] == "WIF Hat"
    assert meta["decimals"] == 6
    assert meta["supply"] == 1000000000000000
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_on_error_response():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(500, json={"error": "oops"}))
        meta = await fetcher.fetch("Whatever")

    assert meta is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_on_missing_result():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": None}))
        meta = await fetcher.fetch("Whatever")

    assert meta is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_handles_missing_optional_fields():
    client = RateLimitedClient(default_rps=100)
    fetcher = TokenMetadataFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    # Token with no metadata content (happens for brand-new tokens)
    stripped = {
        "jsonrpc": "2.0",
        "result": {
            "interface": "FungibleToken",
            "id": "Bare1111111111111111111111111111111111",
            "content": {},
            "token_info": {"decimals": 9, "supply": 0},
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json=stripped))
        meta = await fetcher.fetch("Bare1111111111111111111111111111111111")

    assert meta is not None
    assert meta["symbol"] is None
    assert meta["name"] is None
    assert meta["decimals"] == 9
    await client.aclose()
```

- [ ] **Step 3: Run failing test**

```bash
python -m pytest tests/unit/test_token_metadata.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement token_metadata.py**

Create `enrichment/token_metadata.py`:

```python
"""Fetch token metadata via Helius DAS getAsset."""
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.token_metadata")


class TokenMetadataFetcher:
    """Wraps Helius DAS `getAsset` for fungible tokens.

    Returns a dict of normalized fields, or None if the RPC call fails or
    the response does not contain a usable result. Raising exceptions is
    reserved for bugs; network/remote failures are converted to `None`.
    """

    def __init__(self, http: RateLimitedClient, rpc_url: str):
        self.http = http
        self.rpc_url = rpc_url

    async def fetch(self, mint: str) -> dict[str, Any] | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAsset",
                    "params": {"id": mint},
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getAsset_error", mint=mint, error=str(e))
            return None

        if resp.status_code != 200:
            logger.warning(
                "das_getAsset_non_200", mint=mint, status=resp.status_code
            )
            return None

        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("das_getAsset_bad_json", mint=mint, error=str(e))
            return None

        result = data.get("result")
        if not result or not isinstance(result, dict):
            return None

        content = result.get("content") or {}
        metadata = content.get("metadata") or {}
        token_info = result.get("token_info") or {}

        return {
            "symbol": metadata.get("symbol"),
            "name": metadata.get("name"),
            "description": metadata.get("description"),
            "decimals": token_info.get("decimals"),
            "supply": token_info.get("supply"),
            "mint_authority": token_info.get("mint_authority"),
            "freeze_authority": token_info.get("freeze_authority"),
        }
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/unit/test_token_metadata.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/enrichment/token_metadata.py meme-trading/runner/tests/fixtures/das_getAsset_fungible.json meme-trading/runner/tests/unit/test_token_metadata.py
git commit -m "runner: Helius DAS getAsset token metadata fetcher"
git push
```

---

## Task 8: Price + liquidity fetcher (DexScreener + Jupiter)

**Why:** DexScreener gives us price, liquidity, volume, pair age. Jupiter gives us precise slippage for specific trade sizes via the quote API. These are the inputs the Entry Quality filter and downstream scoring need.

**Approach:** Two independent API calls behind one fetcher. DexScreener returns an array of pairs — we pick the highest-liquidity one on Solana. Jupiter quote is called twice (buy side for 0.25 SOL and 0.5 SOL) to produce the `slippage_at_size_pct` map.

**Files:**
- Create: `enrichment/price_liquidity.py`
- Create: `tests/fixtures/dexscreener_pairs.json`
- Create: `tests/fixtures/jupiter_quote_buy_025.json`
- Create: `tests/fixtures/jupiter_quote_buy_050.json`
- Create: `tests/unit/test_price_liquidity.py`

- [ ] **Step 1: Create the DexScreener fixture**

Create `tests/fixtures/dexscreener_pairs.json`:

```json
{
  "schemaVersion": "1.0.0",
  "pairs": [
    {
      "chainId": "solana",
      "dexId": "raydium",
      "url": "https://dexscreener.com/solana/testpair1",
      "pairAddress": "TestPair1111111111111111111111111111111111",
      "baseToken": {
        "address": "TestMint1111111111111111111111111111111111",
        "name": "WIF Hat",
        "symbol": "WIFHAT"
      },
      "quoteToken": {
        "address": "So11111111111111111111111111111111111111112",
        "name": "Wrapped SOL",
        "symbol": "SOL"
      },
      "priceNative": "0.0000002600",
      "priceUsd": "0.0001",
      "liquidity": {
        "usd": 42000.0,
        "base": 4200000000.0,
        "quote": 162.5
      },
      "volume": {
        "h24": 150000.0,
        "h6": 40000.0,
        "h1": 12000.0
      },
      "pairCreatedAt": 1744371000000
    },
    {
      "chainId": "solana",
      "dexId": "orca",
      "pairAddress": "TestPair2222222222222222222222222222222222",
      "baseToken": {
        "address": "TestMint1111111111111111111111111111111111",
        "symbol": "WIFHAT"
      },
      "quoteToken": {
        "address": "So11111111111111111111111111111111111111112",
        "symbol": "SOL"
      },
      "priceNative": "0.0000002620",
      "priceUsd": "0.000101",
      "liquidity": {
        "usd": 8000.0,
        "base": 800000000.0,
        "quote": 30.8
      },
      "volume": {"h24": 5000.0},
      "pairCreatedAt": 1744370000000
    }
  ]
}
```

- [ ] **Step 2: Create the two Jupiter quote fixtures**

Create `tests/fixtures/jupiter_quote_buy_025.json`:

```json
{
  "inputMint": "So11111111111111111111111111111111111111112",
  "outputMint": "TestMint1111111111111111111111111111111111",
  "inAmount": "250000000",
  "outAmount": "960000000",
  "otherAmountThreshold": "950400000",
  "swapMode": "ExactIn",
  "slippageBps": 50,
  "priceImpactPct": "0.012",
  "routePlan": []
}
```

Create `tests/fixtures/jupiter_quote_buy_050.json`:

```json
{
  "inputMint": "So11111111111111111111111111111111111111112",
  "outputMint": "TestMint1111111111111111111111111111111111",
  "inAmount": "500000000",
  "outAmount": "1870000000",
  "otherAmountThreshold": "1851300000",
  "swapMode": "ExactIn",
  "slippageBps": 50,
  "priceImpactPct": "0.028",
  "routePlan": []
}
```

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_price_liquidity.py`:

```python
"""DexScreener + Jupiter price/liquidity/slippage fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.price_liquidity import PriceLiquidityFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.asyncio
async def test_fetch_picks_highest_liquidity_pair_and_assembles_result():
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    ds = json.loads((FIX / "dexscreener_pairs.json").read_text())
    q025 = json.loads((FIX / "jupiter_quote_buy_025.json").read_text())
    q050 = json.loads((FIX / "jupiter_quote_buy_050.json").read_text())

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/TestMint1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=ds["pairs"]))

        mock.get("https://quote-api.jup.ag/v6/quote").mock(
            side_effect=[
                httpx.Response(200, json=q025),
                httpx.Response(200, json=q050),
            ]
        )

        result = await fetcher.fetch(
            "TestMint1111111111111111111111111111111111",
            sizes_sol=[0.25, 0.5],
        )

    assert result is not None
    # Picks highest liquidity pair (42k > 8k)
    assert result["price_usd"] == pytest.approx(0.0001)
    assert result["price_sol"] == pytest.approx(0.00000026)
    assert result["liquidity_usd"] == pytest.approx(42000.0)
    assert result["volume_24h_usd"] == pytest.approx(150000.0)
    assert result["pair_age_seconds"] is not None
    # Slippage map
    assert result["slippage_at_size_pct"][0.25] == pytest.approx(1.2, abs=0.5)
    assert result["slippage_at_size_pct"][0.5] == pytest.approx(2.8, abs=0.5)

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_dexscreener_empty():
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/Absent1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=[]))

        result = await fetcher.fetch(
            "Absent1111111111111111111111111111111111",
            sizes_sol=[0.25],
        )

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_partial_when_jupiter_fails():
    """DexScreener succeeds, Jupiter fails — result still populated minus slippage."""
    client = RateLimitedClient(default_rps=100)
    fetcher = PriceLiquidityFetcher(client)

    ds = json.loads((FIX / "dexscreener_pairs.json").read_text())

    with respx.mock() as mock:
        mock.get(
            "https://api.dexscreener.com/tokens/v1/solana/TestMint1111111111111111111111111111111111"
        ).mock(return_value=httpx.Response(200, json=ds["pairs"]))
        mock.get("https://quote-api.jup.ag/v6/quote").mock(
            return_value=httpx.Response(500, json={})
        )

        result = await fetcher.fetch(
            "TestMint1111111111111111111111111111111111",
            sizes_sol=[0.25],
        )

    assert result is not None
    assert result["liquidity_usd"] == pytest.approx(42000.0)
    assert result["slippage_at_size_pct"] == {}
    await client.aclose()
```

- [ ] **Step 4: Run failing test**

```bash
python -m pytest tests/unit/test_price_liquidity.py -v
```

Expected: ImportError.

- [ ] **Step 5: Implement price_liquidity.py**

Create `enrichment/price_liquidity.py`:

```python
"""DexScreener + Jupiter price/liquidity/slippage fetcher."""
from datetime import datetime, timezone
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.price_liquidity")

DEXSCREENER_BASE = "https://api.dexscreener.com"
JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"


class PriceLiquidityFetcher:
    """Combine DexScreener pair data with Jupiter quote-based slippage checks."""

    def __init__(self, http: RateLimitedClient):
        self.http = http

    async def fetch(
        self,
        mint: str,
        sizes_sol: list[float] | None = None,
    ) -> dict[str, Any] | None:
        sizes_sol = sizes_sol or [0.25]

        pair = await self._best_pair(mint)
        if pair is None:
            return None

        slippage_map: dict[float, float] = {}
        for size in sizes_sol:
            slip = await self._jupiter_slippage_pct(mint, size)
            if slip is not None:
                slippage_map[size] = slip

        return {
            "price_sol": _as_float(pair.get("priceNative")),
            "price_usd": _as_float(pair.get("priceUsd")),
            "liquidity_usd": _as_float(((pair.get("liquidity") or {}).get("usd"))),
            "volume_24h_usd": _as_float(((pair.get("volume") or {}).get("h24"))),
            "pair_age_seconds": _pair_age_seconds(pair.get("pairCreatedAt")),
            "slippage_at_size_pct": slippage_map,
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
        }

    async def _best_pair(self, mint: str) -> dict | None:
        url = f"{DEXSCREENER_BASE}/tokens/v1/solana/{mint}"
        try:
            resp = await self.http.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("dexscreener_bad_json", mint=mint, error=str(e))
            return None

        # DexScreener returns either a list of pairs or {"pairs": [...]}.
        pairs = body if isinstance(body, list) else (body.get("pairs") or [])
        if not pairs:
            return None

        return max(
            pairs,
            key=lambda p: _as_float(((p.get("liquidity") or {}).get("usd"))) or 0.0,
        )

    async def _jupiter_slippage_pct(self, mint: str, size_sol: float) -> float | None:
        # inAmount is lamports
        in_amount = int(size_sol * 1_000_000_000)
        params = {
            "inputMint": SOL_MINT,
            "outputMint": mint,
            "amount": str(in_amount),
            "slippageBps": "500",
            "swapMode": "ExactIn",
        }
        try:
            resp = await self.http.get(JUPITER_QUOTE, params=params)
        except Exception as e:  # noqa: BLE001
            logger.warning("jupiter_quote_error", mint=mint, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning("jupiter_quote_bad_json", mint=mint, error=str(e))
            return None

        impact = data.get("priceImpactPct")
        if impact is None:
            return None
        try:
            return float(impact) * 100.0
        except (TypeError, ValueError):
            return None


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pair_age_seconds(created_ms: Any) -> int | None:
    if created_ms is None:
        return None
    try:
        created_s = float(created_ms) / 1000.0
    except (TypeError, ValueError):
        return None
    now_s = datetime.now(timezone.utc).timestamp()
    age = int(now_s - created_s)
    return max(age, 0)
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/unit/test_price_liquidity.py -v
```

Expected: 3 passed. Note: the slippage values in test 1 use `abs=0.5` tolerance because `priceImpactPct` from the fixtures is 0.012 and 0.028 (decimal form), multiplied by 100 gives 1.2 and 2.8 — the assertion comes out exact, but `abs=0.5` absorbs any drift if you later tweak the fixture.

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/enrichment/price_liquidity.py meme-trading/runner/tests/fixtures/dexscreener_pairs.json meme-trading/runner/tests/fixtures/jupiter_quote_buy_025.json meme-trading/runner/tests/fixtures/jupiter_quote_buy_050.json meme-trading/runner/tests/unit/test_price_liquidity.py
git commit -m "runner: DexScreener + Jupiter price/liquidity/slippage fetcher"
git push
```

---

## Task 9: Deployer history fetcher

**Why:** The scoring model needs `deployer_address`, `deployer_age_seconds`, and `deployer_token_count` for two hard gates (deployer holdings > 5%, deployer as rug signal) plus the rug/risk sub-score. The deployer is found by reading the first signature against the mint address; its age is the first signature on that wallet; its token count is an approximation via `getSignaturesForAddress` looking for mint-initialization instructions.

**Approach for v1:** Simplify. Use Helius RPC `getSignaturesForAddress` on the mint address with `limit=1000` sorted oldest-first via successive calls. The first entry is the deployer's initialization transaction; parse it to get the payer address (deployer). For deployer age and token count, we don't have a single canonical RPC — so v1 just returns `deployer_address` and a best-effort `deployer_token_count` defaulting to `None` when unknown. The filter layer can still gate on deployer identity, and Plan 2b can tighten this.

**Files:**
- Create: `enrichment/deployer.py`
- Create: `tests/fixtures/helius_signatures_mint_init.json`
- Create: `tests/fixtures/helius_getTransaction_mint_init.json`
- Create: `tests/unit/test_deployer.py`

- [ ] **Step 1: Create fixture — getSignaturesForAddress response**

Create `tests/fixtures/helius_signatures_mint_init.json`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "signature": "DeployerInitSig11111111111111111111111111",
      "slot": 50000000,
      "blockTime": 1744000000,
      "confirmationStatus": "finalized",
      "err": null
    },
    {
      "signature": "LaterTxSig22222222222222222222222222222",
      "slot": 50000100,
      "blockTime": 1744000100,
      "confirmationStatus": "finalized",
      "err": null
    }
  ]
}
```

- [ ] **Step 2: Create fixture — getTransaction for the init tx**

Create `tests/fixtures/helius_getTransaction_mint_init.json`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "blockTime": 1744000000,
    "meta": {
      "err": null,
      "fee": 5000
    },
    "transaction": {
      "message": {
        "accountKeys": [
          {"pubkey": "DeployerWallet111111111111111111111111111", "signer": true, "writable": true},
          {"pubkey": "TestMint1111111111111111111111111111111111", "signer": false, "writable": true},
          {"pubkey": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "signer": false, "writable": false}
        ]
      },
      "signatures": ["DeployerInitSig11111111111111111111111111"]
    }
  }
}
```

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_deployer.py`:

```python
"""Helius-based deployer history fetcher."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from runner.enrichment.deployer import DeployerFetcher
from runner.utils.http import RateLimitedClient

FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.asyncio
async def test_fetch_identifies_deployer_from_earliest_tx():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    sigs_body = json.loads((FIX / "helius_signatures_mint_init.json").read_text())
    tx_body = json.loads((FIX / "helius_getTransaction_mint_init.json").read_text())

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        def _router(request):
            body = json.loads(request.content)
            if body["method"] == "getSignaturesForAddress":
                return httpx.Response(200, json=sigs_body)
            if body["method"] == "getTransaction":
                return httpx.Response(200, json=tx_body)
            return httpx.Response(404, json={})

        mock.post("/rpc").mock(side_effect=_router)

        info = await fetcher.fetch("TestMint1111111111111111111111111111111111")

    assert info is not None
    assert info["deployer_address"] == "DeployerWallet111111111111111111111111111"
    assert info["deployer_first_tx_time"] is not None
    assert info["deployer_age_seconds"] is not None
    assert info["deployer_age_seconds"] > 0

    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_no_signatures():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        mock.post("/rpc").mock(return_value=httpx.Response(200, json={"result": []}))
        info = await fetcher.fetch("Nothing1111111111111111111111111111111")

    assert info is None
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_returns_none_when_tx_has_no_signer():
    client = RateLimitedClient(default_rps=100)
    fetcher = DeployerFetcher(client, rpc_url="https://rpc.helius.test/rpc")

    sigs_body = {
        "jsonrpc": "2.0",
        "result": [
            {"signature": "S1", "blockTime": 1744000000}
        ],
    }
    tx_body = {
        "jsonrpc": "2.0",
        "result": {
            "blockTime": 1744000000,
            "meta": {"err": None},
            "transaction": {
                "message": {"accountKeys": []},
                "signatures": ["S1"],
            },
        },
    }

    with respx.mock(base_url="https://rpc.helius.test") as mock:
        def _router(request):
            body = json.loads(request.content)
            if body["method"] == "getSignaturesForAddress":
                return httpx.Response(200, json=sigs_body)
            return httpx.Response(200, json=tx_body)
        mock.post("/rpc").mock(side_effect=_router)

        info = await fetcher.fetch("NoSigner1111111111111111111111111111111")

    assert info is None
    await client.aclose()
```

- [ ] **Step 4: Run failing test**

```bash
python -m pytest tests/unit/test_deployer.py -v
```

Expected: ImportError.

- [ ] **Step 5: Implement deployer.py**

Create `enrichment/deployer.py`:

```python
"""Helius-based deployer history fetcher.

v1: find deployer by looking at the earliest signature against the mint
address, then parse that transaction's first signer as the deployer.
Age is derived from the blockTime of that init tx.

Deployer token count is deliberately omitted in v1 — getting it reliably
requires scanning the deployer's full signature history, which is slow
and API-intensive. Filter/scoring code treats `deployer_token_count=None`
as unknown rather than zero.
"""
from datetime import datetime, timezone
from typing import Any

from runner.utils.http import RateLimitedClient
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.deployer")


class DeployerFetcher:
    def __init__(self, http: RateLimitedClient, rpc_url: str):
        self.http = http
        self.rpc_url = rpc_url

    async def fetch(self, mint: str) -> dict[str, Any] | None:
        earliest_sig, block_time = await self._earliest_signature(mint)
        if earliest_sig is None:
            return None

        signer = await self._signer_of(earliest_sig)
        if signer is None:
            return None

        age_seconds: int | None = None
        first_tx_time: datetime | None = None
        if block_time is not None:
            first_tx_time = datetime.fromtimestamp(block_time, tz=timezone.utc)
            age_seconds = int(
                (datetime.now(timezone.utc) - first_tx_time).total_seconds()
            )

        return {
            "deployer_address": signer,
            "deployer_first_tx_time": first_tx_time,
            "deployer_age_seconds": age_seconds,
            "deployer_token_count": None,  # not computed in v1
        }

    async def _earliest_signature(self, mint: str) -> tuple[str | None, int | None]:
        """Return (signature, blockTime) of the earliest known tx for this mint."""
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [mint, {"limit": 1000}],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("signatures_error", mint=mint, error=str(e))
            return None, None
        if resp.status_code != 200:
            return None, None

        try:
            body = resp.json()
        except Exception:
            return None, None
        result = body.get("result") or []
        if not result:
            return None, None

        # Result is returned newest-first; find the earliest by blockTime.
        with_time = [e for e in result if e.get("blockTime") is not None]
        if not with_time:
            # Fall back to last element (oldest in result order).
            tail = result[-1]
            return tail.get("signature"), tail.get("blockTime")

        earliest = min(with_time, key=lambda e: e["blockTime"])
        return earliest.get("signature"), earliest.get("blockTime")

    async def _signer_of(self, signature: str) -> str | None:
        try:
            resp = await self.http.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                            "commitment": "confirmed",
                        },
                    ],
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("getTransaction_error", signature=signature, error=str(e))
            return None
        if resp.status_code != 200:
            return None
        try:
            body = resp.json()
        except Exception:
            return None

        result = body.get("result")
        if not result:
            return None
        message = (result.get("transaction") or {}).get("message") or {}
        keys = message.get("accountKeys") or []

        for key in keys:
            if isinstance(key, dict):
                if key.get("signer"):
                    return key.get("pubkey")
            elif isinstance(key, str):
                # Bare-string format — first entry is conventionally the fee payer.
                return key
        return None
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/unit/test_deployer.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/enrichment/deployer.py meme-trading/runner/tests/fixtures/helius_signatures_mint_init.json meme-trading/runner/tests/fixtures/helius_getTransaction_mint_init.json meme-trading/runner/tests/unit/test_deployer.py
git commit -m "runner: deployer history fetcher via Helius getSignaturesForAddress"
git push
```

---

## Task 10: Enricher orchestrator

**Why:** The three fetchers run in parallel via `asyncio.gather`, and an `EnrichedToken` is assembled from their (possibly partial) results. The enricher is a long-running task that consumes `ClusterSignal`s from a queue and produces `EnrichedToken`s on the next queue.

**Files:**
- Create: `enrichment/enricher.py`
- Create: `tests/unit/test_enricher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_enricher.py`:

```python
"""Enricher orchestrator: consumes ClusterSignal, produces EnrichedToken."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.enricher import Enricher
from runner.enrichment.schemas import EnrichedToken


def _sig(mint="MINT") -> ClusterSignal:
    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    return ClusterSignal(
        token_mint=mint,
        wallets=["A1", "A2", "B1"],
        wallet_count=3,
        tier_counts={"A": 2, "B": 1},
        first_buy_time=base,
        last_buy_time=base + timedelta(minutes=10),
        convergence_seconds=600,
        mid_price_sol=0.00025,
    )


@pytest.mark.asyncio
async def test_enricher_assembles_enriched_token_from_all_fetchers():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = {
        "symbol": "WIFHAT",
        "name": "WIF Hat",
        "decimals": 6,
        "supply": 1_000_000_000.0,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.00026,
        "price_usd": 0.0001,
        "liquidity_usd": 42000.0,
        "volume_24h_usd": 150000.0,
        "pair_age_seconds": 1800,
        "slippage_at_size_pct": {0.25: 1.2},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {
        "deployer_address": "Dep1",
        "deployer_first_tx_time": datetime(2026, 4, 4, tzinfo=timezone.utc),
        "deployer_age_seconds": 7 * 24 * 3600,
        "deployer_token_count": None,
    }

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    sig = _sig()
    enriched = await enricher._enrich_one(sig)

    assert isinstance(enriched, EnrichedToken)
    assert enriched.token_mint == "MINT"
    assert enriched.symbol == "WIFHAT"
    assert enriched.price_sol == pytest.approx(0.00026)
    assert enriched.liquidity_usd == pytest.approx(42000.0)
    assert enriched.deployer_address == "Dep1"
    assert enriched.errors == []


@pytest.mark.asyncio
async def test_enricher_collects_errors_when_fetchers_return_none():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = None
    price = AsyncMock()
    price.fetch.return_value = None
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "Dep1"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    enriched = await enricher._enrich_one(_sig())
    assert enriched.symbol is None
    assert enriched.price_sol is None
    assert enriched.deployer_address == "Dep1"
    assert "metadata_unavailable" in enriched.errors
    assert "price_liquidity_unavailable" in enriched.errors
    assert "deployer_unavailable" not in enriched.errors


@pytest.mark.asyncio
async def test_enricher_run_consumes_signal_bus_and_produces_enriched_bus():
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

    metadata = AsyncMock()
    metadata.fetch.return_value = {"symbol": "X", "decimals": 6, "supply": 1e9}
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.0001,
        "liquidity_usd": 10000.0,
        "slippage_at_size_pct": {},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {"deployer_address": "D"}

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    task = asyncio.create_task(enricher.run())
    try:
        await signal_bus.put(_sig())
        enriched = await asyncio.wait_for(enriched_bus.get(), timeout=2.0)
        assert enriched.token_mint == "MINT"
        assert enriched.symbol == "X"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run failing test**

```bash
python -m pytest tests/unit/test_enricher.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement enricher.py**

Create `enrichment/enricher.py`:

```python
"""Enrichment orchestrator — turns a ClusterSignal into an EnrichedToken."""
import asyncio
from datetime import datetime, timezone

from runner.cluster.convergence import ClusterSignal
from runner.enrichment.schemas import EnrichedToken
from runner.utils.logging import get_logger

logger = get_logger("runner.enrichment.enricher")


class Enricher:
    """Run the three enrichment fetchers in parallel and assemble an EnrichedToken.

    Sub-fetcher failures are collected into `EnrichedToken.errors` rather
    than raised, so a single slow/broken API cannot block the pipeline.
    """

    def __init__(
        self,
        signal_bus: asyncio.Queue,
        enriched_bus: asyncio.Queue,
        metadata_fetcher,
        price_fetcher,
        deployer_fetcher,
    ):
        self.signal_bus = signal_bus
        self.enriched_bus = enriched_bus
        self.metadata_fetcher = metadata_fetcher
        self.price_fetcher = price_fetcher
        self.deployer_fetcher = deployer_fetcher

    async def run(self) -> None:
        logger.info("enricher_start")
        while True:
            signal: ClusterSignal = await self.signal_bus.get()
            try:
                enriched = await self._enrich_one(signal)
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "enricher_unexpected_failure",
                    mint=signal.token_mint,
                    error=str(e),
                )
                continue
            await self.enriched_bus.put(enriched)

    async def _enrich_one(self, signal: ClusterSignal) -> EnrichedToken:
        meta_task = asyncio.create_task(self.metadata_fetcher.fetch(signal.token_mint))
        price_task = asyncio.create_task(self.price_fetcher.fetch(signal.token_mint))
        deployer_task = asyncio.create_task(self.deployer_fetcher.fetch(signal.token_mint))

        meta, price, deployer = await asyncio.gather(
            meta_task, price_task, deployer_task, return_exceptions=True
        )

        errors: list[str] = []

        meta = None if isinstance(meta, Exception) or meta is None else meta
        if meta is None:
            errors.append("metadata_unavailable")

        price = None if isinstance(price, Exception) or price is None else price
        if price is None:
            errors.append("price_liquidity_unavailable")

        deployer = (
            None if isinstance(deployer, Exception) or deployer is None else deployer
        )
        if deployer is None:
            errors.append("deployer_unavailable")

        return EnrichedToken(
            token_mint=signal.token_mint,
            cluster_signal=signal,
            enriched_at=datetime.now(timezone.utc),
            symbol=(meta or {}).get("symbol"),
            name=(meta or {}).get("name"),
            decimals=(meta or {}).get("decimals"),
            supply=(meta or {}).get("supply"),
            token_created_at=None,  # derived in Task 11 if needed
            price_sol=(price or {}).get("price_sol"),
            price_usd=(price or {}).get("price_usd"),
            liquidity_usd=(price or {}).get("liquidity_usd"),
            volume_24h_usd=(price or {}).get("volume_24h_usd"),
            pair_age_seconds=(price or {}).get("pair_age_seconds"),
            slippage_at_size_pct=(price or {}).get("slippage_at_size_pct", {}),
            deployer_address=(deployer or {}).get("deployer_address"),
            deployer_age_seconds=(deployer or {}).get("deployer_age_seconds"),
            deployer_token_count=(deployer or {}).get("deployer_token_count"),
            errors=errors,
        )
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/unit/test_enricher.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/enrichment/enricher.py meme-trading/runner/tests/unit/test_enricher.py
git commit -m "runner: enricher orchestrator producing EnrichedToken"
git push
```

---

## Task 11: Wire enricher into main.py + e2e integration test

**Why:** Final wiring task — replace the Phase 3 `_drain` sink with the `Enricher` so the full pipeline flows ingest → cluster → enrichment → sink. Add an integration test that pushes a `BuyEvent` through and asserts an `EnrichedToken` comes out (with mocked fetchers).

**Files:**
- Modify: `main.py`
- Create: `tests/integration/test_ingest_cluster_enrichment.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_ingest_cluster_enrichment.py`:

```python
"""End-to-end: BuyEvent → cluster → enrichment → EnrichedToken."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from runner.cluster.convergence import ConvergenceDetector
from runner.cluster.wallet_tier import Tier, WalletTierCache
from runner.enrichment.enricher import Enricher
from runner.enrichment.schemas import EnrichedToken
from runner.ingest.events import BuyEvent


class _StubTierCache(WalletTierCache):
    def __init__(self, mapping):
        self._map = mapping

    async def load(self):
        pass


@pytest.mark.asyncio
async def test_full_pipeline_produces_enriched_token():
    event_bus: asyncio.Queue = asyncio.Queue()
    signal_bus: asyncio.Queue = asyncio.Queue()
    enriched_bus: asyncio.Queue = asyncio.Queue()

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
        "symbol": "PIPE",
        "name": "Pipeline Token",
        "decimals": 6,
        "supply": 1e9,
    }
    price = AsyncMock()
    price.fetch.return_value = {
        "price_sol": 0.0003,
        "price_usd": 0.0002,
        "liquidity_usd": 25000.0,
        "volume_24h_usd": 80000.0,
        "pair_age_seconds": 1200,
        "slippage_at_size_pct": {0.25: 1.5},
    }
    deployer = AsyncMock()
    deployer.fetch.return_value = {
        "deployer_address": "DepX",
        "deployer_age_seconds": 86400,
        "deployer_token_count": None,
    }

    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata,
        price_fetcher=price,
        deployer_fetcher=deployer,
    )

    det_task = asyncio.create_task(detector.run())
    enr_task = asyncio.create_task(enricher.run())

    base = datetime(2026, 4, 11, 10, 0, tzinfo=timezone.utc)
    for i, (sig, wallet) in enumerate(
        [("s1", "A1"), ("s2", "A2"), ("s3", "B1")]
    ):
        await event_bus.put(
            BuyEvent(
                signature=sig,
                wallet_address=wallet,
                token_mint="PIPE_MINT",
                sol_amount=0.25,
                token_amount=1000,
                price_sol=0.00025,
                block_time=base + timedelta(minutes=i * 5),
            )
        )

    enriched: EnrichedToken = await asyncio.wait_for(enriched_bus.get(), timeout=3.0)
    assert enriched.token_mint == "PIPE_MINT"
    assert enriched.symbol == "PIPE"
    assert enriched.liquidity_usd == pytest.approx(25000.0)
    assert enriched.deployer_address == "DepX"
    assert enriched.cluster_signal.wallet_count == 3

    for t in (det_task, enr_task):
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Run the integration test — expect FAIL (main.py not yet wired)**

Actually, this specific test wires detector + enricher directly without main.py and should pass immediately after Task 10 is complete. Run it now:

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/integration/test_ingest_cluster_enrichment.py -v
```

Expected: 1 passed. If it fails, fix before proceeding.

- [ ] **Step 3: Modify main.py to wire the Enricher**

Open `main.py`. At the top, add imports:

```python
from runner.enrichment.enricher import Enricher
from runner.enrichment.token_metadata import TokenMetadataFetcher
from runner.enrichment.price_liquidity import PriceLiquidityFetcher
from runner.enrichment.deployer import DeployerFetcher
```

Inside `_main()`, after the `detector = ConvergenceDetector(...)` block and before `logger.info("wired", ...)`, add:

```python
    metadata_fetcher = TokenMetadataFetcher(http, rpc_url=settings.helius_rpc_url)
    price_fetcher = PriceLiquidityFetcher(http)
    deployer_fetcher = DeployerFetcher(http, rpc_url=settings.helius_rpc_url)

    enriched_bus: asyncio.Queue = asyncio.Queue()
    enricher = Enricher(
        signal_bus=signal_bus,
        enriched_bus=enriched_bus,
        metadata_fetcher=metadata_fetcher,
        price_fetcher=price_fetcher,
        deployer_fetcher=deployer_fetcher,
    )
```

Update the `asyncio.gather` block — replace the `_supervise(lambda: _drain(signal_bus, logger), "drain", logger)` line with two supervised tasks:

```python
    try:
        results = await asyncio.gather(
            _supervise(monitor.run, "wallet_monitor", logger),
            _supervise(detector.run, "convergence_detector", logger),
            _supervise(enricher.run, "enricher", logger),
            _supervise(lambda: _drain_enriched(enriched_bus, logger), "drain_enriched", logger),
            return_exceptions=True,
        )
        for name, result in zip(
            ["monitor", "detector", "enricher", "drain_enriched"], results
        ):
            if isinstance(result, Exception):
                logger.error("task_exited_with_exception", task=name, error=str(result))
    finally:
        await http.aclose()
        await db.close()
```

Add a new drain function to the file (and remove or leave the old `_drain` unused — remove it for cleanliness):

```python
async def _drain_enriched(enriched_bus: asyncio.Queue, logger) -> None:
    """Phase 4 sink: log every enriched token. Replaced by Filter pipeline in Plan 2b."""
    while True:
        try:
            token = await enriched_bus.get()
            logger.info(
                "enriched_token_drained",
                mint=token.token_mint,
                symbol=token.symbol,
                liquidity_usd=token.liquidity_usd,
                deployer=token.deployer_address,
                errors=token.errors,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("drain_enriched_iteration_error", error=str(e))
```

Delete the old `_drain` function (optional — if you prefer to keep it for historical reference, leave it. Stylistic choice; either is fine.)

- [ ] **Step 4: Run the full suite**

```bash
python -m pytest tests/ -v
```

Expected: approximately 81 passed (55 Plan 1 + 3 bootstrap + 3 buy_events persistence + 2 cluster_signals persistence + 1 hot-reload convergence + 3 enriched_token + 4 token_metadata + 3 price_liquidity + 3 deployer + 3 enricher + 1 integration). The exact count should be 81 after all tasks are complete.

- [ ] **Step 5: Verify main.py imports**

```bash
cd /c/Users/rakai/Leverage/meme-trading
python -c "import sys; sys.path.insert(0, '.'); from runner.main import _main, _drain_enriched, _supervise; print('main ok')"
```

Expected: `main ok`.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/rakai/Leverage
git add meme-trading/runner/main.py meme-trading/runner/tests/integration/test_ingest_cluster_enrichment.py
git commit -m "runner: wire enricher into main.py and add ingest→cluster→enrichment integration test"
git push
```

---

## End-of-plan verification

- [ ] **Step 1: Full test run**

```bash
cd /c/Users/rakai/Leverage/meme-trading/runner
python -m pytest tests/ -v --tb=short
```

Expected: all tests pass. Approximately 81 tests total (55 Plan 1 + 26 Plan 2a).

- [ ] **Step 2: Sanity check the enrichment imports**

```bash
cd /c/Users/rakai/Leverage/meme-trading
python -c "import sys; sys.path.insert(0, '.'); import runner.enrichment.schemas, runner.enrichment.enricher, runner.enrichment.token_metadata, runner.enrichment.price_liquidity, runner.enrichment.deployer; print('enrichment ok')"
```

- [ ] **Step 3: Verify git log is clean**

```bash
cd /c/Users/rakai/Leverage
git log --oneline -15
```

Expected: 11 new commits from this plan, all pushed.

---

## What's next (Plan 2b preview)

Plan 2a ends with: `ClusterSignal` → `EnrichedToken` flowing through the pipeline, with persistence and hot-reloadable weights landed.

**Plan 2b** (next plan) covers:
- **Filter pipeline:** `FilterResult` + `BaseFilter` abstract, then `RugGate` (RugCheck), `HolderFilter` (Helius DAS token accounts), `InsiderFilter` (RugCheck insiders graph), `EntryQualityFilter` (extension + liquidity), `FollowThroughProbe` (5-min async probe)
- **Scoring engine:** `FactorScorer` (per-filter sub-scores), `RunnerScorer` (weighted combine), `VerdictAssigner` (Ignore/Watch/Strong/Probable), `Explainer` (evidence payload for Telegram alerts)
- **Persistence:** `filter_results` and `runner_scores` tables
- **Integration:** wire the full scoring pipeline into `main.py`

End state after Plan 2b: scored verdicts written to DB for every candidate, ready for Plan 3 (executor + Telegram alerts + dashboard).
