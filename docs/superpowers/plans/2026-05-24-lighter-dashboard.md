# Lighter Trading Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only web dashboard that visualizes the Lighter paper-trading bridge's performance and live state.

**Architecture:** A standalone `lighter-dashboard` container (FastAPI + Jinja templates + HTMX + Tailwind CDN) on srv1370094. It reads the bridge's SQLite DB and fetches live order-book mids from Lighter's public REST. It never calls into the bridge process. Served behind the existing Traefik with basic-auth at `lighter.agentneo.cloud`.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, HTMX (CDN), Tailwind (CDN), lighter-sdk (for REST mark fetch), pytest. SQLite read access.

---

## Important context for the implementer

- The bridge lives at `scripts/lighter-bridge/`. Its DB schema is defined in `scripts/lighter-bridge/src/lighter_bridge/db.py`. Three tables: `trade_log`, `signal_log`, `account_snapshot`. **Read that file before starting** to confirm column names.
- On the VPS the live DB is at `/docker/lighter-paper/data/lighter_paper.db`.
- Symbols & market IDs (from `scripts/lighter-bridge/config.yaml`): `ZEC` = market_id 90, `SOL` = market_id 2. Lighter host: `https://mainnet.zklighter.elliot.ai`. Initial paper collateral: `2000` USDC.
- Live PnL for an open position: `long → (mark - entry) * base_amount`; `short → (entry - mark) * base_amount`.

### SQLite WAL + read-only nuance (READ THIS)

The design calls for WAL mode on the bridge DB. **A WAL-mode database cannot be opened by a strictly read-only filesystem mount**, because any reader must be able to create/write the `-wal` and `-shm` sidecar files. Therefore:

- The dashboard container mounts the bridge's **data directory read-write** (not `:ro`), so SQLite can manage the sidecar files.
- Safety is enforced at the SQLite connection level instead: every dashboard connection runs `PRAGMA query_only = ON;`, and the dashboard code only ever issues `SELECT`. A `query_only` connection rejects any write attempt with an error.
- Net effect: the dashboard is functionally read-only and cannot mutate trade data, while WAL still gives lock-free concurrent reads. This is the standard one-writer/many-readers SQLite pattern.

---

## File Structure

```
scripts/lighter-dashboard/
├─ src/lighter_dashboard/
│  ├─ __init__.py
│  ├─ config.py        # load YAML config (db path, host, symbols, refresh ms)
│  ├─ stats.py         # PURE metric functions (win%, PF, drawdown, unrealized pnl)
│  ├─ db.py            # read-only SQLite query layer (query_only=ON)
│  ├─ marks.py         # Lighter REST order-book mid + ~2s cache
│  └─ app.py           # FastAPI app, routes, panel rendering
├─ templates/
│  ├─ index.html       # full page shell (HTMX + Tailwind CDN, dark theme)
│  └─ partials/
│     ├─ kpis.html
│     ├─ positions.html
│     ├─ equity.html
│     ├─ closed_trades.html
│     ├─ exit_reasons.html
│     ├─ per_symbol.html
│     └─ signals.html
├─ tests/
│  ├─ conftest.py      # builds a fixture SQLite mirroring the bridge schema
│  ├─ test_stats.py
│  ├─ test_db.py
│  └─ test_marks.py
├─ config.yaml
├─ run_dashboard.py    # uvicorn entrypoint
├─ requirements.txt
├─ Dockerfile
└─ docker-compose.yml
```

Plus one modification to the bridge: `scripts/lighter-bridge/src/lighter_bridge/db.py` (enable WAL).

---

## Task 1: Project scaffold

**Files:**
- Create: `scripts/lighter-dashboard/requirements.txt`
- Create: `scripts/lighter-dashboard/config.yaml`
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/__init__.py`
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/config.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi>=0.110
uvicorn[standard]>=0.29
jinja2>=3.1
pyyaml>=6.0
lighter-sdk>=1.0.9
pytest>=8.0
httpx>=0.27
```

- [ ] **Step 2: Create config.yaml**

```yaml
# Lighter dashboard config
db_path: "data/lighter_paper.db"        # bridge DB (mounted in container)
lighter_host: "https://mainnet.zklighter.elliot.ai"
initial_collateral_usdc: 2000
symbols:                                  # name -> market_id
  ZEC: 90
  SOL: 2
refresh:
  live_ms: 3000                           # KPI + positions poll interval
  static_ms: 15000                        # tables + charts poll interval
mark_cache_ttl_s: 2.0
```

- [ ] **Step 3: Create empty package init**

`scripts/lighter-dashboard/src/lighter_dashboard/__init__.py`:

```python
"""Lighter trading dashboard — read-only web view of the paper bridge."""
```

- [ ] **Step 4: Create config loader**

`scripts/lighter-dashboard/src/lighter_dashboard/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class DashboardConfig:
    db_path: str
    lighter_host: str
    initial_collateral_usdc: float
    symbols: dict[str, int]          # name -> market_id
    live_ms: int
    static_ms: int
    mark_cache_ttl_s: float


def load_config(path: str | Path) -> DashboardConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    refresh = data.get("refresh", {})
    return DashboardConfig(
        db_path=data["db_path"],
        lighter_host=data["lighter_host"],
        initial_collateral_usdc=float(data["initial_collateral_usdc"]),
        symbols={k: int(v) for k, v in data["symbols"].items()},
        live_ms=int(refresh.get("live_ms", 3000)),
        static_ms=int(refresh.get("static_ms", 15000)),
        mark_cache_ttl_s=float(data.get("mark_cache_ttl_s", 2.0)),
    )
```

- [ ] **Step 5: Commit**

```bash
git add scripts/lighter-dashboard/requirements.txt scripts/lighter-dashboard/config.yaml scripts/lighter-dashboard/src/lighter_dashboard/__init__.py scripts/lighter-dashboard/src/lighter_dashboard/config.py
git commit -m "feat(dashboard): scaffold lighter-dashboard package + config loader"
```

---

## Task 2: Pure stats module (TDD)

**Files:**
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/stats.py`
- Test: `scripts/lighter-dashboard/tests/test_stats.py`

- [ ] **Step 1: Write the failing tests**

`scripts/lighter-dashboard/tests/test_stats.py`:

```python
import math

from lighter_dashboard import stats


def test_win_rate_basic():
    assert stats.win_rate([10.0, -5.0, 3.0, -2.0]) == 0.5


def test_win_rate_empty():
    assert stats.win_rate([]) == 0.0


def test_profit_factor():
    # gross win 13, gross loss 7
    assert math.isclose(stats.profit_factor([10.0, -5.0, 3.0, -2.0]), 13 / 7)


def test_profit_factor_no_losses_returns_none():
    assert stats.profit_factor([10.0, 3.0]) is None


def test_max_drawdown():
    # equity peaks at 120 then dips to 90 -> drawdown -30
    series = [100.0, 120.0, 90.0, 110.0]
    assert stats.max_drawdown(series) == -30.0


def test_max_drawdown_monotonic_up():
    assert stats.max_drawdown([100.0, 110.0, 130.0]) == 0.0


def test_unrealized_pnl_long():
    assert stats.unrealized_pnl("long", entry=100.0, mark=105.0, base=2.0) == 10.0


def test_unrealized_pnl_short():
    assert stats.unrealized_pnl("short", entry=100.0, mark=95.0, base=2.0) == 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lighter_dashboard.stats'`

- [ ] **Step 3: Write the implementation**

`scripts/lighter-dashboard/src/lighter_dashboard/stats.py`:

```python
"""Pure metric functions. No DB, no network — fully unit-testable."""
from __future__ import annotations

from typing import Optional


def win_rate(pnls: list[float]) -> float:
    closed = [p for p in pnls if p is not None]
    if not closed:
        return 0.0
    wins = sum(1 for p in closed if p > 0)
    return wins / len(closed)


def profit_factor(pnls: list[float]) -> Optional[float]:
    """Gross win / gross loss. None when there are no losses (undefined)."""
    gross_win = sum(p for p in pnls if p and p > 0)
    gross_loss = -sum(p for p in pnls if p and p < 0)
    if gross_loss == 0:
        return None
    return gross_win / gross_loss


def max_drawdown(equity_series: list[float]) -> float:
    """Largest peak-to-trough drop in the series. Returns <= 0.0."""
    peak = float("-inf")
    mdd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


def unrealized_pnl(side: str, entry: float, mark: float, base: float) -> float:
    if side == "long":
        return (mark - entry) * base
    return (entry - mark) * base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_stats.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/lighter-dashboard/src/lighter_dashboard/stats.py scripts/lighter-dashboard/tests/test_stats.py
git commit -m "feat(dashboard): pure stats module (win%, PF, drawdown, unrealized pnl)"
```

---

## Task 3: Read-only DB query layer (TDD)

**Files:**
- Create: `scripts/lighter-dashboard/tests/conftest.py`
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/db.py`
- Test: `scripts/lighter-dashboard/tests/test_db.py`

- [ ] **Step 1: Create the fixture DB builder**

`scripts/lighter-dashboard/tests/conftest.py`:

```python
import sqlite3

import pytest

# Mirrors scripts/lighter-bridge/src/lighter_bridge/db.py SCHEMA.
SCHEMA = """
CREATE TABLE trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side TEXT NOT NULL,
    entry_price REAL, exit_price REAL, initial_sl REAL,
    margin_usdt REAL, leverage REAL, base_amount REAL, notional REAL,
    exit_reason TEXT, pnl_usdt REAL, pnl_pct_account REAL,
    duration_secs INTEGER, max_state INTEGER,
    opened_at TEXT, closed_at TEXT, bar_time_open TEXT,
    slope_pct REAL, body_atr_ratio REAL, adx_at_entry REAL
);
CREATE TABLE signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, side TEXT NOT NULL, bar_time TEXT NOT NULL,
    outcome TEXT, ema9 REAL, slope_pct REAL, body_atr_ratio REAL, detected_at TEXT
);
CREATE TABLE account_snapshot (
    ts TEXT NOT NULL, collateral REAL, portfolio_value REAL,
    n_open INTEGER, cum_pnl REAL
);
"""


@pytest.fixture
def fixture_db(tmp_path):
    path = tmp_path / "lighter_paper.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    # Two closed trades (1 win, 1 loss) + one open trade.
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, exit_price, "
        "margin_usdt, leverage, base_amount, notional, exit_reason, pnl_usdt, "
        "max_state, opened_at, closed_at) VALUES "
        "(1,'ZEC','short',640.0,600.0,250,30,5.0,7500,'manual',200.0,2,"
        "'2026-05-22T15:30:00+00:00','2026-05-23T04:17:44+00:00')"
    )
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, exit_price, "
        "margin_usdt, leverage, base_amount, notional, exit_reason, pnl_usdt, "
        "max_state, opened_at, closed_at) VALUES "
        "(2,'ZEC','short',635.0,642.0,250,30,5.0,7500,'sl',-35.0,0,"
        "'2026-05-23T21:50:00+00:00','2026-05-23T22:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO trade_log (id, symbol, side, entry_price, "
        "margin_usdt, leverage, base_amount, notional, opened_at) VALUES "
        "(3,'SOL','long',85.0,250,30,88.0,7500,'2026-05-23T23:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO signal_log (symbol, side, bar_time, outcome, slope_pct, detected_at) "
        "VALUES ('ZEC','long','2026-05-23T22:00:00+00:00','fired',0.18,"
        "'2026-05-23T22:00:05+00:00')"
    )
    conn.execute(
        "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
        "VALUES ('2026-05-23T00:00:00+00:00',2000,2000,0,0)"
    )
    conn.execute(
        "INSERT INTO account_snapshot (ts, collateral, portfolio_value, n_open, cum_pnl) "
        "VALUES ('2026-05-23T12:00:00+00:00',2200,2200,1,200)"
    )
    conn.commit()
    conn.close()
    return str(path)
```

- [ ] **Step 2: Write the failing tests**

`scripts/lighter-dashboard/tests/test_db.py`:

```python
import pytest

from lighter_dashboard.db import DashboardDB


def test_open_trades(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.open_trades()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SOL"
    assert rows[0]["side"] == "long"
    assert rows[0]["entry_price"] == 85.0


def test_closed_trades_newest_first(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.closed_trades(limit=10)
    assert [r["id"] for r in rows] == [2, 1]
    assert rows[0]["exit_reason"] == "sl"


def test_closed_pnls(fixture_db):
    db = DashboardDB(fixture_db)
    assert sorted(db.closed_pnls()) == [-35.0, 200.0]


def test_per_symbol_stats(fixture_db):
    db = DashboardDB(fixture_db)
    by = {r["symbol"]: r for r in db.per_symbol_stats()}
    assert by["ZEC"]["n"] == 2
    assert by["ZEC"]["net"] == 165.0


def test_exit_reason_mix(fixture_db):
    db = DashboardDB(fixture_db)
    mix = {r["exit_reason"]: r for r in db.exit_reason_mix()}
    assert mix["sl"]["n"] == 1
    assert mix["manual"]["n"] == 1


def test_signals(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.signals(limit=10)
    assert rows[0]["outcome"] == "fired"


def test_snapshots(fixture_db):
    db = DashboardDB(fixture_db)
    rows = db.snapshots()
    assert len(rows) == 2
    assert rows[-1]["portfolio_value"] == 2200


def test_query_only_blocks_writes(fixture_db):
    db = DashboardDB(fixture_db)
    with pytest.raises(Exception):
        with db._conn() as c:
            c.execute("INSERT INTO signal_log (symbol, side, bar_time) VALUES ('X','long','t')")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lighter_dashboard.db'`

- [ ] **Step 4: Write the implementation**

`scripts/lighter-dashboard/src/lighter_dashboard/db.py`:

```python
"""Read-only query layer over the bridge's SQLite DB.

Every connection sets PRAGMA query_only=ON so the dashboard can never
mutate trade data, even though WAL requires a writable directory mount.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class DashboardDB:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def open_trades(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, symbol, side, entry_price, base_amount, margin_usdt, "
                "leverage, notional, opened_at FROM trade_log "
                "WHERE closed_at IS NULL ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_trades(self, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, symbol, side, entry_price, exit_price, exit_reason, "
                "pnl_usdt, max_state, opened_at, closed_at FROM trade_log "
                "WHERE closed_at IS NOT NULL ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def closed_pnls(self) -> list[float]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT pnl_usdt FROM trade_log WHERE pnl_usdt IS NOT NULL"
            ).fetchall()
        return [float(r["pnl_usdt"]) for r in rows]

    def per_symbol_stats(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, COUNT(*) AS n, "
                "SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins, "
                "ROUND(SUM(pnl_usdt), 2) AS net "
                "FROM trade_log WHERE pnl_usdt IS NOT NULL "
                "GROUP BY symbol ORDER BY symbol"
            ).fetchall()
        return [dict(r) for r in rows]

    def exit_reason_mix(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT exit_reason, COUNT(*) AS n, ROUND(SUM(pnl_usdt), 2) AS net "
                "FROM trade_log WHERE exit_reason IS NOT NULL "
                "GROUP BY exit_reason ORDER BY n DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def signals(self, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, side, bar_time, outcome, slope_pct, detected_at "
                "FROM signal_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def snapshots(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, collateral, portfolio_value, n_open, cum_pnl "
                "FROM account_snapshot ORDER BY ts"
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_db.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add scripts/lighter-dashboard/tests/conftest.py scripts/lighter-dashboard/src/lighter_dashboard/db.py scripts/lighter-dashboard/tests/test_db.py
git commit -m "feat(dashboard): read-only DB query layer (query_only enforced)"
```

---

## Task 4: Live mark fetcher with cache (TDD)

**Files:**
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/marks.py`
- Test: `scripts/lighter-dashboard/tests/test_marks.py`

- [ ] **Step 1: Write the failing tests**

`scripts/lighter-dashboard/tests/test_marks.py`:

```python
import pytest

from lighter_dashboard.marks import MarkCache


class _FakeOrder:
    def __init__(self, price):
        self.price = str(price)


class _FakeBook:
    def __init__(self, ask, bid):
        self.asks = [_FakeOrder(ask)]
        self.bids = [_FakeOrder(bid)]


class _FakeOrderApi:
    def __init__(self):
        self.calls = 0
        self.book = _FakeBook(101.0, 99.0)

    async def order_book_orders(self, market_id, limit):
        self.calls += 1
        return self.book


@pytest.mark.asyncio
async def test_get_mid_computes_midpoint():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=2.0)
    mc._order_api = _FakeOrderApi()
    assert await mc.get_mid("SOL") == 100.0


@pytest.mark.asyncio
async def test_get_mid_uses_cache_within_ttl():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=100.0)
    fake = _FakeOrderApi()
    mc._order_api = fake
    await mc.get_mid("SOL")
    await mc.get_mid("SOL")
    assert fake.calls == 1  # second call served from cache


@pytest.mark.asyncio
async def test_get_mid_serves_stale_on_error():
    mc = MarkCache(host="x", symbols={"SOL": 2}, ttl=0.0)
    fake = _FakeOrderApi()
    mc._order_api = fake
    first = await mc.get_mid("SOL")

    class _Boom:
        async def order_book_orders(self, market_id, limit):
            raise RuntimeError("REST down")

    mc._order_api = _Boom()
    assert await mc.get_mid("SOL") == first  # last good value
```

Note: `pytest-asyncio` is required. Add it in Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_marks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lighter_dashboard.marks'`

- [ ] **Step 3: Add pytest-asyncio to requirements**

Append to `scripts/lighter-dashboard/requirements.txt`:

```
pytest-asyncio>=0.23
```

Create `scripts/lighter-dashboard/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 4: Write the implementation**

`scripts/lighter-dashboard/src/lighter_dashboard/marks.py`:

```python
"""Live order-book mid fetcher with a short TTL cache.

Independent of the bridge: makes its own Lighter public REST calls via
lighter.OrderApi.order_book_orders and computes (best_ask + best_bid)/2 —
the same mid the bridge's state machine uses, but sourced separately.
"""
from __future__ import annotations

import time
from typing import Optional

import lighter


class MarkCache:
    def __init__(self, host: str, symbols: dict[str, int], ttl: float = 2.0):
        self.host = host
        self.symbols = symbols            # name -> market_id
        self.ttl = ttl
        self._cache: dict[str, tuple[float, float]] = {}  # name -> (price, monotonic)
        self._api: Optional[lighter.ApiClient] = None
        self._order_api = None            # set lazily or injected in tests

    async def _ensure_api(self):
        if self._order_api is None:
            self._api = lighter.ApiClient(
                configuration=lighter.Configuration(host=self.host)
            )
            self._order_api = lighter.OrderApi(self._api)

    async def get_mid(self, name: str) -> Optional[float]:
        now = time.monotonic()
        cached = self._cache.get(name)
        if cached and now - cached[1] < self.ttl:
            return cached[0]
        await self._ensure_api()
        mid = await self._fetch_mid(name)
        if mid is not None:
            self._cache[name] = (mid, now)
            return mid
        return cached[0] if cached else None   # serve stale on failure

    async def _fetch_mid(self, name: str) -> Optional[float]:
        market_id = self.symbols[name]
        try:
            ob = await self._order_api.order_book_orders(market_id=market_id, limit=1)
        except Exception:
            return None
        if not ob.asks or not ob.bids:
            return None
        ask = float(ob.asks[0].price)
        bid = float(ob.bids[0].price)
        if ask <= 0 or bid <= 0:
            return None
        return (ask + bid) / 2.0

    async def close(self):
        if self._api is not None:
            try:
                await self._api.close()
            except Exception:
                pass
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_marks.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add scripts/lighter-dashboard/src/lighter_dashboard/marks.py scripts/lighter-dashboard/tests/test_marks.py scripts/lighter-dashboard/requirements.txt scripts/lighter-dashboard/pytest.ini
git commit -m "feat(dashboard): live order-book mid fetcher with TTL cache"
```

---

## Task 5: FastAPI app + index shell + KPI/positions partials (TDD)

**Files:**
- Create: `scripts/lighter-dashboard/src/lighter_dashboard/app.py`
- Create: `scripts/lighter-dashboard/templates/index.html`
- Create: `scripts/lighter-dashboard/templates/partials/kpis.html`
- Create: `scripts/lighter-dashboard/templates/partials/positions.html`
- Test: `scripts/lighter-dashboard/tests/test_app.py`

- [ ] **Step 1: Write the failing tests**

`scripts/lighter-dashboard/tests/test_app.py`:

```python
from fastapi.testclient import TestClient

from lighter_dashboard.app import create_app
from lighter_dashboard.config import DashboardConfig


def _cfg(db_path):
    return DashboardConfig(
        db_path=db_path, lighter_host="x", initial_collateral_usdc=2000,
        symbols={"ZEC": 90, "SOL": 2}, live_ms=3000, static_ms=15000,
        mark_cache_ttl_s=2.0,
    )


class _StubMarks:
    async def get_mid(self, name):
        return 90.0   # SOL open trade entry 85 long -> +$15 unrealized at base 88? see fixture
    async def close(self):
        pass


def test_index_renders(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Lighter" in r.text
    assert "hx-get" in r.text          # HTMX wiring present


def test_kpis_partial(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/kpis")
    assert r.status_code == 200
    assert "Equity" in r.text


def test_positions_partial_shows_open(fixture_db):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get("/panel/positions")
    assert r.status_code == 200
    assert "SOL" in r.text             # the one open trade
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lighter_dashboard.app'`

- [ ] **Step 3: Write the FastAPI app**

`scripts/lighter-dashboard/src/lighter_dashboard/app.py`:

```python
"""FastAPI app for the Lighter dashboard.

Routes:
  GET /                -> full page shell (templates/index.html)
  GET /panel/kpis      -> KPI strip partial (live, 3s)
  GET /panel/positions -> open positions partial (live, 3s)
  GET /panel/equity    -> equity curve data + svg (static, 15s)
  GET /panel/closed    -> recent closed trades (static, 15s)
  GET /panel/exits     -> exit-reason mix (static, 15s)
  GET /panel/symbols   -> per-symbol stats (static, 15s)
  GET /panel/signals   -> signal log (static, 15s)

Basic-auth is handled by Traefik in front of this app, so there is no
auth code here.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import stats
from .config import DashboardConfig
from .db import DashboardDB
from .marks import MarkCache

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def create_app(cfg: DashboardConfig, marks=None) -> FastAPI:
    app = FastAPI(title="Lighter Dashboard")
    db = DashboardDB(cfg.db_path)
    mark_cache = marks if marks is not None else MarkCache(
        cfg.lighter_host, cfg.symbols, ttl=cfg.mark_cache_ttl_s
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    async def _open_positions_with_pnl() -> list[dict]:
        out = []
        for row in db.open_trades():
            mark = await mark_cache.get_mid(row["symbol"])
            upnl = None
            if mark is not None:
                upnl = stats.unrealized_pnl(
                    row["side"], row["entry_price"], mark, row["base_amount"]
                )
            out.append({**row, "mark": mark, "upnl": upnl})
        return out

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            "index.html", {"request": request, "cfg": cfg}
        )

    @app.get("/panel/kpis", response_class=HTMLResponse)
    async def panel_kpis(request: Request):
        pnls = db.closed_pnls()
        positions = await _open_positions_with_pnl()
        realized = sum(pnls)
        unrealized = sum(p["upnl"] or 0 for p in positions)
        equity = cfg.initial_collateral_usdc + realized + unrealized
        snaps = [s["portfolio_value"] for s in db.snapshots()] + [equity]
        ctx = {
            "request": request,
            "equity": equity,
            "equity_pct": (equity / cfg.initial_collateral_usdc - 1) * 100,
            "n_open": len(positions),
            "realized": realized,
            "n_closed": len(pnls),
            "win_rate": stats.win_rate(pnls) * 100,
            "profit_factor": stats.profit_factor(pnls),
            "max_dd": stats.max_drawdown(snaps),
        }
        return templates.TemplateResponse("partials/kpis.html", ctx)

    @app.get("/panel/positions", response_class=HTMLResponse)
    async def panel_positions(request: Request):
        return templates.TemplateResponse(
            "partials/positions.html",
            {"request": request, "positions": await _open_positions_with_pnl()},
        )

    @app.get("/panel/closed", response_class=HTMLResponse)
    async def panel_closed(request: Request):
        return templates.TemplateResponse(
            "partials/closed_trades.html",
            {"request": request, "trades": db.closed_trades(limit=20)},
        )

    @app.get("/panel/exits", response_class=HTMLResponse)
    async def panel_exits(request: Request):
        return templates.TemplateResponse(
            "partials/exit_reasons.html",
            {"request": request, "mix": db.exit_reason_mix()},
        )

    @app.get("/panel/symbols", response_class=HTMLResponse)
    async def panel_symbols(request: Request):
        rows = []
        for r in db.per_symbol_stats():
            n, wins = r["n"], (r["wins"] or 0)
            rows.append({**r, "win_pct": (wins / n * 100) if n else 0})
        return templates.TemplateResponse(
            "partials/per_symbol.html", {"request": request, "rows": rows}
        )

    @app.get("/panel/signals", response_class=HTMLResponse)
    async def panel_signals(request: Request):
        return templates.TemplateResponse(
            "partials/signals.html",
            {"request": request, "signals": db.signals(limit=30)},
        )

    @app.get("/panel/equity", response_class=HTMLResponse)
    async def panel_equity(request: Request):
        snaps = db.snapshots()
        values = [s["portfolio_value"] for s in snaps]
        points = _svg_points(values, width=600, height=200)
        return templates.TemplateResponse(
            "partials/equity.html",
            {"request": request, "points": points, "has_data": len(values) > 1},
        )

    return app


def _svg_points(values: list[float], width: int, height: int) -> str:
    """Map a value series to an SVG polyline 'points' string."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = i / (n - 1) * width
        y = height - (v - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)
```

- [ ] **Step 4: Write the index shell template**

`scripts/lighter-dashboard/templates/index.html`:

```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lighter Dashboard</title>
  <script src="https://unpkg.com/htmx.org@1.9.12"></script>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background:#0f1115; color:#e5e7eb; font-size:15px; }
    .panel { background:#1a1d24; border:1px solid #2a2e38; border-radius:10px; padding:16px; }
    .label { font-size:11px; letter-spacing:1px; color:#6b7280; text-transform:uppercase; }
    table { font-size:14px; }
    th { color:#6b7280; font-weight:500; text-align:left; }
    td, th { padding:4px 8px; }
    .pos { color:#10b981; } .neg { color:#ef4444; } .mut { color:#94a3b8; }
  </style>
</head>
<body class="p-5">
  <header class="flex items-center justify-between mb-5">
    <h1 class="text-xl font-bold">Lighter <span class="mut font-normal">paper bridge</span></h1>
    <span class="label">{{ cfg.symbols.keys()|join(' · ') }}</span>
  </header>

  <!-- A: KPI strip (live) -->
  <div hx-get="/panel/kpis" hx-trigger="load, every {{ cfg.live_ms // 1000 }}s" class="mb-4"></div>

  <!-- B: Equity curve (static) -->
  <div hx-get="/panel/equity" hx-trigger="load, every {{ cfg.static_ms // 1000 }}s" class="mb-4"></div>

  <div class="grid grid-cols-3 gap-4 mb-4">
    <!-- C: Open positions (live) -->
    <div hx-get="/panel/positions" hx-trigger="load, every {{ cfg.live_ms // 1000 }}s"></div>
    <!-- D: Closed trades (static) -->
    <div class="col-span-2" hx-get="/panel/closed" hx-trigger="load, every {{ cfg.static_ms // 1000 }}s"></div>
  </div>

  <div class="grid grid-cols-3 gap-4">
    <!-- E: Exit reasons --><div hx-get="/panel/exits" hx-trigger="load, every {{ cfg.static_ms // 1000 }}s"></div>
    <!-- F: Per-symbol --><div hx-get="/panel/symbols" hx-trigger="load, every {{ cfg.static_ms // 1000 }}s"></div>
    <!-- G: Signals --><div hx-get="/panel/signals" hx-trigger="load, every {{ cfg.static_ms // 1000 }}s"></div>
  </div>
</body>
</html>
```

- [ ] **Step 5: Write the KPI + positions partials**

`scripts/lighter-dashboard/templates/partials/kpis.html`:

```html
<div class="grid grid-cols-5 gap-3">
  <div class="panel">
    <div class="label">Equity</div>
    <div class="text-2xl font-bold">${{ "%.2f"|format(equity) }}</div>
    <div class="{{ 'pos' if equity_pct >= 0 else 'neg' }}">{{ "%+.2f"|format(equity_pct) }}% all-time</div>
  </div>
  <div class="panel">
    <div class="label">Open positions</div>
    <div class="text-2xl font-bold">{{ n_open }}</div>
  </div>
  <div class="panel">
    <div class="label">Realized</div>
    <div class="text-2xl font-bold {{ 'pos' if realized >= 0 else 'neg' }}">${{ "%+.2f"|format(realized) }}</div>
    <div class="mut">{{ n_closed }} trades · {{ "%.0f"|format(win_rate) }}% win</div>
  </div>
  <div class="panel">
    <div class="label">Profit factor</div>
    <div class="text-2xl font-bold">{{ "%.2f"|format(profit_factor) if profit_factor is not none else "—" }}</div>
  </div>
  <div class="panel">
    <div class="label">Max drawdown</div>
    <div class="text-2xl font-bold">${{ "%.2f"|format(max_dd) }}</div>
  </div>
</div>
```

`scripts/lighter-dashboard/templates/partials/positions.html`:

```html
<div class="panel">
  <div class="label mb-2">Open positions</div>
  {% if positions %}
    {% for p in positions %}
    <div class="mb-2 p-2 rounded" style="background:#0f1115;">
      <div class="flex justify-between">
        <span class="{{ 'neg' if p.side == 'short' else 'pos' }} font-semibold">{{ p.symbol }} {{ p.side|upper }}</span>
        <span class="{{ 'pos' if (p.upnl or 0) >= 0 else 'neg' }}">
          {{ "${:+,.2f}".format(p.upnl) if p.upnl is not none else "—" }}
        </span>
      </div>
      <div class="mut" style="font-size:13px;">
        entry ${{ "%.4f"|format(p.entry_price) }} ·
        mark {{ "${:.4f}".format(p.mark) if p.mark is not none else "stale" }}
      </div>
    </div>
    {% endfor %}
  {% else %}
    <div class="mut">No open positions</div>
  {% endif %}
</div>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_app.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add scripts/lighter-dashboard/src/lighter_dashboard/app.py scripts/lighter-dashboard/templates/index.html scripts/lighter-dashboard/templates/partials/kpis.html scripts/lighter-dashboard/templates/partials/positions.html scripts/lighter-dashboard/tests/test_app.py
git commit -m "feat(dashboard): FastAPI app + index shell + KPI/positions panels"
```

---

## Task 6: Remaining panel templates

**Files:**
- Create: `scripts/lighter-dashboard/templates/partials/closed_trades.html`
- Create: `scripts/lighter-dashboard/templates/partials/exit_reasons.html`
- Create: `scripts/lighter-dashboard/templates/partials/per_symbol.html`
- Create: `scripts/lighter-dashboard/templates/partials/signals.html`
- Create: `scripts/lighter-dashboard/templates/partials/equity.html`

The routes for these already exist (Task 5). The `test_app.py` tests already pass for routes that have templates; this task adds the missing templates so all routes render. After creating them, extend the test.

- [ ] **Step 1: Add a test that every panel route returns 200**

Append to `scripts/lighter-dashboard/tests/test_app.py`:

```python
import pytest


@pytest.mark.parametrize("path", [
    "/panel/closed", "/panel/exits", "/panel/symbols",
    "/panel/signals", "/panel/equity",
])
def test_all_panels_render(fixture_db, path):
    app = create_app(_cfg(fixture_db), marks=_StubMarks())
    client = TestClient(app)
    r = client.get(path)
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_app.py -k all_panels -v`
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: partials/closed_trades.html`

- [ ] **Step 3: Create closed_trades.html**

`scripts/lighter-dashboard/templates/partials/closed_trades.html`:

```html
<div class="panel">
  <div class="label mb-2">Recent closed trades</div>
  <table class="w-full">
    <thead><tr><th>id</th><th>sym</th><th>side</th><th>entry</th><th>exit</th><th>pnl</th><th>reason</th></tr></thead>
    <tbody>
      {% for t in trades %}
      <tr>
        <td>{{ t.id }}</td><td>{{ t.symbol }}</td>
        <td class="{{ 'neg' if t.side == 'short' else 'pos' }}">{{ t.side }}</td>
        <td>{{ "%.4f"|format(t.entry_price) }}</td>
        <td>{{ "%.4f"|format(t.exit_price) if t.exit_price is not none else "—" }}</td>
        <td class="{{ 'pos' if (t.pnl_usdt or 0) >= 0 else 'neg' }}">{{ "%+.2f"|format(t.pnl_usdt) }}</td>
        <td class="mut">{{ t.exit_reason }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 4: Create exit_reasons.html**

`scripts/lighter-dashboard/templates/partials/exit_reasons.html`:

```html
<div class="panel">
  <div class="label mb-2">Exit reasons</div>
  {% for r in mix %}
  <div class="flex justify-between py-1">
    <span>{{ r.exit_reason }}</span>
    <span class="{{ 'pos' if (r.net or 0) >= 0 else 'neg' }}">{{ r.n }} · ${{ "%+.0f"|format(r.net) }}</span>
  </div>
  {% else %}
  <div class="mut">No closed trades yet</div>
  {% endfor %}
</div>
```

- [ ] **Step 5: Create per_symbol.html**

`scripts/lighter-dashboard/templates/partials/per_symbol.html`:

```html
<div class="panel">
  <div class="label mb-2">Per-symbol stats</div>
  <table class="w-full">
    <thead><tr><th>sym</th><th>n</th><th>win%</th><th>net</th></tr></thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td>{{ r.symbol }}</td><td>{{ r.n }}</td>
        <td>{{ "%.0f"|format(r.win_pct) }}%</td>
        <td class="{{ 'pos' if (r.net or 0) >= 0 else 'neg' }}">${{ "%+.0f"|format(r.net) }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 6: Create signals.html**

`scripts/lighter-dashboard/templates/partials/signals.html`:

```html
<div class="panel">
  <div class="label mb-2">Signal log</div>
  {% for s in signals %}
  <div class="py-1" style="font-size:13px;">
    <span class="mut">{{ s.bar_time[11:16] if s.bar_time else "" }}</span>
    {{ s.symbol }} {{ s.side }}
    <span class="{{ 'pos' if s.outcome == 'fired' else ('mut' if s.outcome == 'expired' else 'neg') }}">{{ s.outcome }}</span>
    {% if s.slope_pct is not none %}<span class="mut">· slope {{ "%.2f"|format(s.slope_pct) }}</span>{% endif %}
  </div>
  {% else %}
  <div class="mut">No signals yet</div>
  {% endfor %}
</div>
```

- [ ] **Step 7: Create equity.html**

`scripts/lighter-dashboard/templates/partials/equity.html`:

```html
<div class="panel">
  <div class="label mb-2">Equity curve</div>
  {% if has_data %}
  <div style="height:200px; background:linear-gradient(180deg, rgba(16,185,129,0.12), rgba(16,185,129,0));">
    <svg viewBox="0 0 600 200" preserveAspectRatio="none" style="width:100%; height:100%;">
      <polyline fill="none" stroke="#10b981" stroke-width="2" points="{{ points }}"/>
    </svg>
  </div>
  {% else %}
  <div class="mut">Not enough snapshots yet</div>
  {% endif %}
</div>
```

- [ ] **Step 8: Run all app tests to verify they pass**

Run: `cd scripts/lighter-dashboard && PYTHONPATH=src python -m pytest tests/test_app.py -v`
Expected: PASS (all)

- [ ] **Step 9: Commit**

```bash
git add scripts/lighter-dashboard/templates/partials/ scripts/lighter-dashboard/tests/test_app.py
git commit -m "feat(dashboard): remaining panels (closed trades, exits, per-symbol, signals, equity)"
```

---

## Task 7: Uvicorn entrypoint + local manual run

**Files:**
- Create: `scripts/lighter-dashboard/run_dashboard.py`

- [ ] **Step 1: Write the entrypoint**

`scripts/lighter-dashboard/run_dashboard.py`:

```python
"""Uvicorn entrypoint. Usage: python run_dashboard.py --config config.yaml"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lighter_dashboard.app import create_app          # noqa: E402
from lighter_dashboard.config import load_config       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    cfg = load_config(args.config)
    app = create_app(cfg)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manual smoke test against a copy of the live DB**

Copy a real DB locally first (read-only safe), then run:

```bash
cd scripts/lighter-dashboard
# point config.yaml db_path at a local copy for the smoke test, or pass a test DB
PYTHONPATH=src python run_dashboard.py --config config.yaml --port 8080
```

Open `http://localhost:8080`. Expected: page loads, KPI strip + panels populate, live panels refresh every 3s. Verify numbers against:
`sqlite3 -header -column <db> 'SELECT COUNT(*), ROUND(SUM(pnl_usdt),2) FROM trade_log WHERE closed_at IS NOT NULL'`

- [ ] **Step 3: Commit**

```bash
git add scripts/lighter-dashboard/run_dashboard.py
git commit -m "feat(dashboard): uvicorn entrypoint"
```

---

## Task 8: Enable WAL on the bridge DB (one strategy-neutral change)

**Files:**
- Modify: `scripts/lighter-bridge/src/lighter_bridge/db.py` (the `__init__` method, around lines 60-66)

- [ ] **Step 1: Add the WAL pragma**

In `scripts/lighter-bridge/src/lighter_bridge/db.py`, in `TradeLogDB.__init__`, after `self.conn = sqlite3.connect(str(self.path))` and before `self.conn.executescript(SCHEMA)`, add:

```python
        # WAL mode: lock-free concurrent reads for the dashboard process.
        # Strategy-neutral — affects only write persistence, not trading logic.
        self.conn.execute("PRAGMA journal_mode=WAL;")
```

- [ ] **Step 2: Verify the bridge still imports and starts**

Run: `cd scripts/lighter-bridge && PYTHONPATH=src python -c "from lighter_bridge.db import TradeLogDB; d=TradeLogDB('data/_waltest.db'); print(d.conn.execute('PRAGMA journal_mode;').fetchone()); d.close()"`
Expected: prints `('wal',)`

- [ ] **Step 3: Clean up the test DB**

```bash
rm -f scripts/lighter-bridge/data/_waltest.db scripts/lighter-bridge/data/_waltest.db-wal scripts/lighter-bridge/data/_waltest.db-shm
```

- [ ] **Step 4: Commit**

```bash
git add scripts/lighter-bridge/src/lighter_bridge/db.py
git commit -m "feat(lighter-bridge): enable SQLite WAL for concurrent dashboard reads"
```

---

## Task 9: Containerization (Dockerfile + compose + Traefik labels)

**Files:**
- Create: `scripts/lighter-dashboard/Dockerfile`
- Create: `scripts/lighter-dashboard/docker-compose.yml`
- Create: `scripts/lighter-dashboard/.dockerignore`

- [ ] **Step 1: Create .dockerignore**

`scripts/lighter-dashboard/.dockerignore`:

```
venv/
__pycache__/
tests/
*.db
*.db-wal
*.db-shm
.superpowers/
```

- [ ] **Step 2: Create the Dockerfile**

`scripts/lighter-dashboard/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY templates/ ./templates/
COPY config.yaml run_dashboard.py ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app/src \
    TZ=America/Sao_Paulo

EXPOSE 8080

CMD ["python", "-u", "run_dashboard.py", "--config", "config.yaml", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 3: Create docker-compose.yml with Traefik labels**

`scripts/lighter-dashboard/docker-compose.yml`:

```yaml
services:
  lighter-dashboard:
    build: .
    image: lighter-dashboard:latest
    container_name: lighter-dashboard
    restart: unless-stopped
    volumes:
      # Read-write mount REQUIRED for WAL sidecar files (-wal/-shm).
      # The app enforces read-only at the SQLite layer (PRAGMA query_only=ON).
      - /docker/lighter-paper/data:/app/data
    networks:
      - traefik
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.lighter-dash.rule=Host(`lighter.agentneo.cloud`)"
      - "traefik.http.routers.lighter-dash.entrypoints=websecure"
      - "traefik.http.routers.lighter-dash.tls.certresolver=letsencrypt"
      - "traefik.http.routers.lighter-dash.middlewares=lighter-auth"
      - "traefik.http.middlewares.lighter-auth.basicauth.usersfile=/auth/lighter.htpasswd"
      - "traefik.http.services.lighter-dash.loadbalancer.server.port=8080"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  traefik:
    external: true
    name: traefik-mncm_default
```

NOTE: the external network name and the certresolver name must match the live Traefik setup. Verify on the VPS at deploy time (Task 10, Step 1) and adjust these two values if they differ.

- [ ] **Step 4: Commit**

```bash
git add scripts/lighter-dashboard/Dockerfile scripts/lighter-dashboard/docker-compose.yml scripts/lighter-dashboard/.dockerignore
git commit -m "feat(dashboard): Dockerfile + compose with Traefik basic-auth labels"
```

---

## Task 10: Deploy to VPS (manual, with Rich)

This task is operational — run with Rich present for the DNS + auth steps. **Do not run destructive commands; the bridge must keep running.**

- [ ] **Step 1: Verify the live Traefik network name + certresolver**

```bash
ssh root@46.202.146.30 "docker inspect traefik-mncm-traefik-1 --format '{{json .NetworkSettings.Networks}}' | tr ',' '\n'"
ssh root@46.202.146.30 "grep -r certresolver /docker/traefik-mncm/ | head"
```
Adjust `docker-compose.yml` network name + `certresolver` if they differ from the assumptions in Task 9.

- [ ] **Step 2: Rich adds the Cloudflare DNS record**

`lighter.agentneo.cloud` → A record → `46.202.146.30` (proxied or DNS-only per Rich's preference). Prompt Rich to do this; wait for confirmation.

- [ ] **Step 3: Generate the basic-auth htpasswd (password never committed)**

```bash
ssh root@46.202.146.30 "mkdir -p /docker/lighter-dashboard/auth && \
  docker run --rm httpd:2.4-alpine htpasswd -nbB radk9 '!Lighter987' > /docker/lighter-dashboard/auth/lighter.htpasswd && \
  cat /docker/lighter-dashboard/auth/lighter.htpasswd"
```
Mount this file into Traefik (or use a Traefik dynamic-config file). Confirm the Traefik basic-auth `usersfile` path matches the mount. The plaintext password lives only in this command and the resulting hash — never in git.

- [ ] **Step 4: Deploy WAL change to the bridge + restart**

```bash
scp scripts/lighter-bridge/src/lighter_bridge/db.py root@46.202.146.30:/docker/lighter-paper/src/lighter_bridge/db.py
ssh root@46.202.146.30 "cd /docker/lighter-paper && docker compose restart"
ssh root@46.202.146.30 "sqlite3 /docker/lighter-paper/data/lighter_paper.db 'PRAGMA journal_mode;'"
```
Expected last command: `wal`. Confirm the bridge restarted cleanly and restored any open positions (check logs).

- [ ] **Step 5: Copy the dashboard to the VPS + build + up**

```bash
ssh root@46.202.146.30 "mkdir -p /docker/lighter-dashboard"
scp -r scripts/lighter-dashboard/{src,templates,config.yaml,run_dashboard.py,requirements.txt,Dockerfile,docker-compose.yml,.dockerignore} root@46.202.146.30:/docker/lighter-dashboard/
ssh root@46.202.146.30 "cd /docker/lighter-dashboard && docker compose up -d --build"
```

- [ ] **Step 6: Verify end to end**

- `https://lighter.agentneo.cloud` prompts for basic-auth, accepts `radk9` / the password.
- All seven panels render; KPI + positions refresh every 3s.
- Numbers match `sqlite3` queries against the live DB.
- Confirm the bridge is unaffected: `ssh root@46.202.146.30 "docker ps --filter name=lighter-bridge"` shows it up, and recent trade/heartbeat logs continue.

- [ ] **Step 7: Final commit (any deploy-time config adjustments)**

```bash
git add scripts/lighter-dashboard/docker-compose.yml scripts/lighter-dashboard/config.yaml
git commit -m "chore(dashboard): deploy-time config (network name, certresolver)"
git push
```

---

## Self-Review

**Spec coverage:**
- Isolation / separate container → Task 9 (compose), Task 5 (no bridge calls). ✓
- Read-only DB access → Task 3 (`query_only=ON`) + Task 9 mount note. ✓ (WAL nuance documented; strict `:ro` replaced by connection-level enforcement — flagged to Rich.)
- Independent live marks → Task 4 (`marks.py`). ✓
- WAL change → Task 8. ✓
- 7 panels (A–G) → Tasks 5 + 6. ✓
- FastAPI + HTMX + Tailwind → Tasks 5/6 (CDN). ✓
- Basic-auth at Traefik, username radk9 → Task 9 labels + Task 10 htpasswd (password hashed, never committed). ✓
- Domain lighter.agentneo.cloud → Task 9 label + Task 10 DNS. ✓
- Dark theme + 14–16px text floor → Task 5 index.html base styles (`font-size:15px`, table `14px`). ✓
- Refresh 3s live / 15s static → Task 5 index.html hx-trigger. ✓
- Error handling: stale marks + no-data panels → Task 4 (serve stale), Task 5/6 (`{% else %}` empty states). ✓
- Testing: stats/db/marks/app → Tasks 2,3,4,5,6. ✓

**Placeholder scan:** No TBD/TODO. The only deploy-time unknowns (Traefik network name, certresolver) are explicitly verified in Task 10 Step 1 with a fallback instruction — not placeholders in code.

**Type consistency:** `DashboardConfig` fields match between `config.py`, `app.py`, and `test_app.py`. `MarkCache.get_mid` signature matches its use in `app.py` and tests. `DashboardDB` method names (`open_trades`, `closed_trades`, `closed_pnls`, `per_symbol_stats`, `exit_reason_mix`, `signals`, `snapshots`) match between `db.py`, `app.py`, and `test_db.py`. `stats.unrealized_pnl(side, entry, mark, base)` matches its call site in `app.py`.
