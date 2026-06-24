# Lighter Market-Data Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the Lighter per-IP 429 storm by routing every paper bridge's Lighter REST reads through one shared, caching, rate-limited gateway on the VPS.

**Architecture:** A standalone async HTTP service (`lighter-gateway`) transparently reverse-proxies Lighter read endpoints with a per-path TTL cache, single-flight request coalescing, and a global upstream token-bucket rate cap. All four live bridges (apex, reclaim, rebound, scalper) change one config line — `connection.host` → `http://lighter-gateway:8060` — and join a shared Docker network. The box's upstream Lighter footprint collapses from `N bridges × coins` to `1 × unique coins`, permanently.

**Tech Stack:** Python 3.12, aiohttp (server + upstream client), PyYAML, pytest. Docker Compose with a shared external network.

## Global Constraints

- Python 3.12; async/await throughout. No new heavy deps beyond `aiohttp` + `PyYAML`.
- Gateway is **read-only proxy**: only `GET` is cached/forwarded. Any non-GET is forwarded uncached (paper bridges never POST to Lighter, but be safe).
- Only `2xx` JSON responses are cached. Non-2xx is never cached.
- Cache key = `(method, path, sorted(query_pairs))` — query-order-independent.
- Gateway binds inside Docker on a shared network `lighter-net` as service `lighter-gateway`, container port `8060`. Bridges reach it at `http://lighter-gateway:8060`.
- `restart: always` on the gateway container.
- Do NOT change any strategy logic, entry/exit, sizing, signals, or DB schemas. This is plumbing only.
- Live bridges in scope: `scripts/apex`, `scripts/reclaim-bridge`, `scripts/boost-bridge` (rebound), `scripts/scalper-bridge`. Dead bridges (squeeze, z-fade, lighter-bridge, blofin, hlsm, smc) are OUT of scope.
- New gateway package lives at `scripts/marketdata/`.
- Cutover order on the VPS: deploy gateway → verify healthy → cut **apex** first → confirm bars/marks fresh → cut reclaim, rebound, scalper.
- Per-bridge rollback = revert `connection.host` to `https://mainnet.zklighter.elliot.ai` and restart. Independent and instant.

---

### Task 1: Gateway package scaffold + config loader

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/__init__.py`
- Create: `scripts/marketdata/src/lighter_gateway/config.py`
- Create: `scripts/marketdata/config.yaml`
- Create: `scripts/marketdata/requirements.txt`
- Create: `scripts/marketdata/pyproject.toml`
- Test: `scripts/marketdata/tests/test_config.py`

**Interfaces:**
- Produces: `load_config(path: str) -> GatewayConfig` where
  `GatewayConfig(upstream: str, host: str, port: int, rate_per_s: float, burst: float, max_stale_s: float, cache_capacity: int, ttl: dict[str, float], default_ttl: float)`.
  `ttl_for(self, path: str) -> float` returns the longest matching path-prefix TTL, else `default_ttl`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/marketdata/tests/test_config.py
from lighter_gateway.config import load_config

def test_load_and_ttl_prefix_match(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "upstream: https://up.example\n"
        "listen: {host: 0.0.0.0, port: 8060}\n"
        "rate_limit: {rate_per_s: 4.0, burst: 8}\n"
        "max_stale_s: 15\n"
        "cache_capacity: 1000\n"
        "ttl:\n"
        "  /api/v1/candlesticks: 20\n"
        "  /api/v1/orderBook: 2.5\n"
        "  default: 2.0\n"
    )
    c = load_config(str(p))
    assert c.upstream == "https://up.example"
    assert c.port == 8060
    assert c.rate_per_s == 4.0
    assert c.ttl_for("/api/v1/candlesticks?x=1".split("?")[0]) == 20
    assert c.ttl_for("/api/v1/orderBookOrders") == 2.5   # longest prefix
    assert c.ttl_for("/api/v1/unknown") == 2.0           # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: lighter_gateway`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/marketdata/src/lighter_gateway/config.py
from __future__ import annotations
from dataclasses import dataclass, field
import yaml


@dataclass
class GatewayConfig:
    upstream: str
    host: str
    port: int
    rate_per_s: float
    burst: float
    max_stale_s: float
    cache_capacity: int
    ttl: dict[str, float] = field(default_factory=dict)
    default_ttl: float = 2.0

    def ttl_for(self, path: str) -> float:
        best_len, best_ttl = -1, self.default_ttl
        for prefix, secs in self.ttl.items():
            if path.startswith(prefix) and len(prefix) > best_len:
                best_len, best_ttl = len(prefix), secs
        return best_ttl


def load_config(path: str) -> GatewayConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    listen = raw.get("listen", {})
    rl = raw.get("rate_limit", {})
    ttl = dict(raw.get("ttl", {}))
    default_ttl = float(ttl.pop("default", 2.0))
    return GatewayConfig(
        upstream=raw["upstream"].rstrip("/"),
        host=str(listen.get("host", "0.0.0.0")),
        port=int(listen.get("port", 8060)),
        rate_per_s=float(rl.get("rate_per_s", 4.0)),
        burst=float(rl.get("burst", 8.0)),
        max_stale_s=float(raw.get("max_stale_s", 15.0)),
        cache_capacity=int(raw.get("cache_capacity", 2000)),
        ttl={str(k): float(v) for k, v in ttl.items()},
        default_ttl=default_ttl,
    )
```

```yaml
# scripts/marketdata/config.yaml
upstream: "https://mainnet.zklighter.elliot.ai"
listen: { host: "0.0.0.0", port: 8060 }
rate_limit: { rate_per_s: 4.0, burst: 8 }   # hard upstream ceiling for the whole box
max_stale_s: 15                              # serve cache past TTL up to this when throttled
cache_capacity: 4000
ttl:
  "/api/v1/candlesticks": 20      # 5m bars; cheap freshness
  "/api/v1/orderBook": 2.5        # marks; matches today's 3s mark poll (no freshness loss)
  "/api/v1/orderBooks": 300       # market-config metadata, near-static
  default: 2.0
```

```
# scripts/marketdata/requirements.txt
aiohttp>=3.9
PyYAML>=6.0
```

```toml
# scripts/marketdata/pyproject.toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "lighter-gateway"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["aiohttp>=3.9", "PyYAML>=6.0"]

[tool.pytest.ini_options]
pythonpath = ["src"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests / 1 test function green).

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/
git commit -m "feat(gateway): config loader + scaffold for Lighter market-data gateway"
```

---

### Task 2: TTL+LRU response cache

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/cache.py`
- Test: `scripts/marketdata/tests/test_cache.py`

**Interfaces:**
- Produces:
  - `CachedResponse(status: int, body: bytes, content_type: str, fetched_monotonic: float)`
  - `ResponseCache(capacity: int)` with `get(key: str) -> CachedResponse | None` (returns entry regardless of age; touches LRU), `put(key: str, entry: CachedResponse) -> None` (LRU-evicts oldest beyond capacity).
- Age is computed by the caller as `now_monotonic - entry.fetched_monotonic`; the cache stores, it does not expire.

- [ ] **Step 1: Write the failing test**

```python
# scripts/marketdata/tests/test_cache.py
from lighter_gateway.cache import ResponseCache, CachedResponse

def _e(ts, body=b"x"):
    return CachedResponse(status=200, body=body, content_type="application/json", fetched_monotonic=ts)

def test_get_returns_put_entry():
    c = ResponseCache(capacity=2)
    c.put("a", _e(1.0, b"A"))
    got = c.get("a")
    assert got is not None and got.body == b"A"
    assert c.get("missing") is None

def test_lru_eviction_by_capacity():
    c = ResponseCache(capacity=2)
    c.put("a", _e(1.0)); c.put("b", _e(2.0))
    c.get("a")                      # touch 'a' so 'b' is now LRU
    c.put("c", _e(3.0))             # evicts 'b'
    assert c.get("a") is not None
    assert c.get("b") is None
    assert c.get("c") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && python -m pytest tests/test_cache.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/marketdata/src/lighter_gateway/cache.py
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class CachedResponse:
    status: int
    body: bytes
    content_type: str
    fetched_monotonic: float


class ResponseCache:
    """In-memory LRU store of upstream responses. Stores only; TTL/staleness
    decisions are the caller's (it knows per-path TTL and the clock)."""

    def __init__(self, capacity: int):
        self._cap = max(1, capacity)
        self._d: "OrderedDict[str, CachedResponse]" = OrderedDict()

    def get(self, key: str) -> CachedResponse | None:
        entry = self._d.get(key)
        if entry is not None:
            self._d.move_to_end(key)
        return entry

    def put(self, key: str, entry: CachedResponse) -> None:
        self._d[key] = entry
        self._d.move_to_end(key)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/test_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/src/lighter_gateway/cache.py scripts/marketdata/tests/test_cache.py
git commit -m "feat(gateway): TTL+LRU response cache"
```

---

### Task 3: Async token-bucket rate limiter

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/ratelimit.py`
- Test: `scripts/marketdata/tests/test_ratelimit.py`

**Interfaces:**
- Produces: `TokenBucket(rate_per_s: float, burst: float, clock: Callable[[], float] = time.monotonic)` with `try_acquire() -> bool` (non-blocking; consumes one token if available, refilling by elapsed time since last call, capped at `burst`).

- [ ] **Step 1: Write the failing test**

```python
# scripts/marketdata/tests/test_ratelimit.py
from lighter_gateway.ratelimit import TokenBucket

def test_burst_then_throttle_then_refill():
    t = [0.0]
    b = TokenBucket(rate_per_s=2.0, burst=3.0, clock=lambda: t[0])
    assert [b.try_acquire() for _ in range(3)] == [True, True, True]  # burst
    assert b.try_acquire() is False                                   # empty
    t[0] = 0.5                                                        # +0.5s -> +1 token
    assert b.try_acquire() is True
    assert b.try_acquire() is False
    t[0] = 10.0                                                       # long gap caps at burst
    assert [b.try_acquire() for _ in range(3)] == [True, True, True]
    assert b.try_acquire() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && python -m pytest tests/test_ratelimit.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/marketdata/src/lighter_gateway/ratelimit.py
from __future__ import annotations
import time
from typing import Callable


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: float,
                 clock: Callable[[], float] = time.monotonic):
        self._rate = float(rate_per_s)
        self._burst = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._last = clock()

    def try_acquire(self) -> bool:
        now = self._clock()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/test_ratelimit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/src/lighter_gateway/ratelimit.py scripts/marketdata/tests/test_ratelimit.py
git commit -m "feat(gateway): async token-bucket upstream rate limiter"
```

---

### Task 4: Single-flight request coalescing

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/singleflight.py`
- Test: `scripts/marketdata/tests/test_singleflight.py`

**Interfaces:**
- Produces: `SingleFlight()` with `async do(key: str, factory: Callable[[], Awaitable[T]]) -> T` — concurrent calls with the same key share ONE `factory()` execution and all receive its result (or its exception).

- [ ] **Step 1: Write the failing test**

```python
# scripts/marketdata/tests/test_singleflight.py
import asyncio
import pytest
from lighter_gateway.singleflight import SingleFlight

@pytest.mark.asyncio
async def test_concurrent_same_key_runs_once():
    sf = SingleFlight()
    calls = {"n": 0}
    async def factory():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return "result"
    results = await asyncio.gather(*[sf.do("k", factory) for _ in range(5)])
    assert results == ["result"] * 5
    assert calls["n"] == 1          # coalesced to a single execution

@pytest.mark.asyncio
async def test_different_keys_run_independently():
    sf = SingleFlight()
    calls = {"n": 0}
    async def factory():
        calls["n"] += 1
        return calls["n"]
    await asyncio.gather(sf.do("a", factory), sf.do("b", factory))
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && python -m pytest tests/test_singleflight.py -v`
Expected: FAIL (`ModuleNotFoundError`). (`pytest-asyncio` is added in this step's requirements.)

- [ ] **Step 3: Write minimal implementation**

Add `pytest-asyncio>=0.23` to `scripts/marketdata/requirements.txt`, and to `pyproject.toml` add:
```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
asyncio_mode = "auto"
```

```python
# scripts/marketdata/src/lighter_gateway/singleflight.py
from __future__ import annotations
import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    """Deduplicate concurrent async calls sharing a key onto one execution."""

    def __init__(self):
        self._inflight: dict[str, asyncio.Future] = {}

    async def do(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            result = await factory()
            fut.set_result(result)
            return result
        except Exception as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/test_singleflight.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/src/lighter_gateway/singleflight.py scripts/marketdata/tests/test_singleflight.py scripts/marketdata/requirements.txt scripts/marketdata/pyproject.toml
git commit -m "feat(gateway): single-flight request coalescing"
```

---

### Task 5: Gateway core (cache + single-flight + rate-limit orchestration)

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/core.py`
- Test: `scripts/marketdata/tests/test_core.py`

**Interfaces:**
- Consumes: `GatewayConfig` (Task 1), `ResponseCache`/`CachedResponse` (Task 2), `TokenBucket` (Task 3), `SingleFlight` (Task 4).
- Produces:
  - `GatewayResponse(status: int, body: bytes, content_type: str, source: str)` where `source ∈ {"hit","miss","stale","throttled"}`.
  - `Gateway(cfg, fetch, *, clock=time.monotonic, cache=None, bucket=None)` where
    `fetch: Callable[[str, str, str], Awaitable[tuple[int, bytes, str]]]` takes `(method, path, query_string)` and returns `(status, body, content_type)`.
  - `async handle(method: str, path: str, query: str) -> GatewayResponse`.
  - `stats() -> dict` with counters: `upstream_calls, hits, misses, stale_served, throttled, upstream_non2xx`.
- Behavior:
  - Non-GET → always fetch, never cache.
  - GET: fresh cache (age ≤ `ttl_for(path)`) → `hit`.
  - Else single-flight fetch: re-check fresh cache inside; if a token is available → upstream `fetch`, cache only `2xx`, return `miss`; if no token → serve any cached entry with age ≤ `max_stale_s` as `stale`, else `GatewayResponse(503, ..., "throttled")`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/marketdata/tests/test_core.py
import pytest
from lighter_gateway.config import GatewayConfig
from lighter_gateway.core import Gateway

def _cfg(**kw):
    base = dict(upstream="https://up", host="0.0.0.0", port=8060, rate_per_s=1.0,
                burst=2.0, max_stale_s=30.0, cache_capacity=100,
                ttl={"/api/v1/orderBook": 2.5}, default_ttl=2.0)
    base.update(kw)
    return GatewayConfig(**base)

def make_fetch(counter, status=200, body=b'{"ok":1}', ct="application/json"):
    async def fetch(method, path, query):
        counter["n"] += 1
        return (status, body, ct)
    return fetch

@pytest.mark.asyncio
async def test_cache_hit_skips_upstream():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    r1 = await gw.handle("GET", "/api/v1/orderBook", "market_id=2")
    assert r1.source == "miss" and n["n"] == 1
    t[0] = 1.0                                   # within 2.5s TTL
    r2 = await gw.handle("GET", "/api/v1/orderBook", "market_id=2")
    assert r2.source == "hit" and n["n"] == 1    # no extra upstream call

@pytest.mark.asyncio
async def test_ttl_expiry_refetches():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")
    t[0] = 3.0                                    # past 2.5s TTL
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert r.source == "miss" and n["n"] == 2

@pytest.mark.asyncio
async def test_throttle_serves_stale():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(rate_per_s=0.0, burst=1.0), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")   # consumes the 1 burst token
    t[0] = 5.0                                            # past TTL, no refill (rate 0)
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert r.source == "stale" and n["n"] == 1           # served old body, no upstream

@pytest.mark.asyncio
async def test_query_order_independent_key():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "a=1&b=2")
    r = await gw.handle("GET", "/api/v1/orderBook", "b=2&a=1")
    assert r.source == "hit" and n["n"] == 1

@pytest.mark.asyncio
async def test_non_2xx_not_cached():
    t = [0.0]; n = {"n": 0}
    gw = Gateway(_cfg(), make_fetch(n, status=429, body=b"slow down"), clock=lambda: t[0])
    await gw.handle("GET", "/api/v1/orderBook", "m=2")
    r = await gw.handle("GET", "/api/v1/orderBook", "m=2")
    assert n["n"] == 2 and r.status == 429               # never cached, refetched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && python -m pytest tests/test_core.py -v`
Expected: FAIL (`ModuleNotFoundError: lighter_gateway.core`).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/marketdata/src/lighter_gateway/core.py
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode

from .cache import CachedResponse, ResponseCache
from .config import GatewayConfig
from .ratelimit import TokenBucket
from .singleflight import SingleFlight

FetchFn = Callable[[str, str, str], Awaitable[tuple[int, bytes, str]]]


@dataclass
class GatewayResponse:
    status: int
    body: bytes
    content_type: str
    source: str   # hit | miss | stale | throttled | passthrough


def _norm_query(query: str) -> str:
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    return urlencode(pairs)


class Gateway:
    def __init__(self, cfg: GatewayConfig, fetch: FetchFn, *,
                 clock: Callable[[], float] = time.monotonic,
                 cache: ResponseCache | None = None,
                 bucket: TokenBucket | None = None):
        self.cfg = cfg
        self._fetch = fetch
        self._clock = clock
        self._cache = cache or ResponseCache(cfg.cache_capacity)
        self._bucket = bucket or TokenBucket(cfg.rate_per_s, cfg.burst, clock=clock)
        self._sf = SingleFlight()
        self._stats = {"upstream_calls": 0, "hits": 0, "misses": 0,
                       "stale_served": 0, "throttled": 0, "upstream_non2xx": 0}

    def stats(self) -> dict:
        return dict(self._stats)

    def _key(self, method: str, path: str, query: str) -> str:
        return f"{method} {path}?{_norm_query(query)}"

    async def handle(self, method: str, path: str, query: str) -> GatewayResponse:
        if method.upper() != "GET":
            status, body, ct = await self._upstream(method, path, query)
            return GatewayResponse(status, body, ct, "passthrough")

        key = self._key(method, path, query)
        ttl = self.cfg.ttl_for(path)
        entry = self._cache.get(key)
        if entry is not None and (self._clock() - entry.fetched_monotonic) <= ttl:
            self._stats["hits"] += 1
            return GatewayResponse(entry.status, entry.body, entry.content_type, "hit")

        async def factory() -> GatewayResponse:
            # Re-check: a concurrent caller may have just refreshed.
            e2 = self._cache.get(key)
            if e2 is not None and (self._clock() - e2.fetched_monotonic) <= ttl:
                self._stats["hits"] += 1
                return GatewayResponse(e2.status, e2.body, e2.content_type, "hit")
            if not self._bucket.try_acquire():
                # Throttled: serve any acceptably-fresh stale copy, else 503.
                if e2 is not None and (self._clock() - e2.fetched_monotonic) <= self.cfg.max_stale_s:
                    self._stats["stale_served"] += 1
                    return GatewayResponse(e2.status, e2.body, e2.content_type, "stale")
                self._stats["throttled"] += 1
                return GatewayResponse(503, b'{"error":"gateway throttled"}',
                                       "application/json", "throttled")
            status, body, ct = await self._upstream(method, path, query)
            if 200 <= status < 300:
                self._cache.put(key, CachedResponse(status, body, ct, self._clock()))
            else:
                self._stats["upstream_non2xx"] += 1
            self._stats["misses"] += 1
            return GatewayResponse(status, body, ct, "miss")

        return await self._sf.do(key, factory)

    async def _upstream(self, method: str, path: str, query: str):
        self._stats["upstream_calls"] += 1
        return await self._fetch(method, path, query)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/test_core.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/src/lighter_gateway/core.py scripts/marketdata/tests/test_core.py
git commit -m "feat(gateway): core cache+singleflight+ratelimit orchestration"
```

---

### Task 6: aiohttp server + upstream client + entrypoint

**Files:**
- Create: `scripts/marketdata/src/lighter_gateway/upstream.py`
- Create: `scripts/marketdata/src/lighter_gateway/server.py`
- Create: `scripts/marketdata/run_gateway.py`
- Test: `scripts/marketdata/tests/test_server.py`

**Interfaces:**
- Consumes: `Gateway` (Task 5), `load_config` (Task 1).
- Produces:
  - `upstream.make_fetch(session: aiohttp.ClientSession, base_url: str) -> FetchFn`.
  - `server.build_app(gw: Gateway) -> aiohttp.web.Application` with routes: `GET /__gw/health` → `{"ok": true}`; `GET /__gw/stats` → `gw.stats()`; catch-all `/{tail:.*}` (any method) → `gw.handle(...)` returning upstream status/body/content-type.
  - `run_gateway.py`: load config, open one `ClientSession`, build app, `web.run_app(host, port)`.

- [ ] **Step 1: Write the failing test** (uses aiohttp's pytest test server; the upstream is faked at the `Gateway` level)

```python
# scripts/marketdata/tests/test_server.py
import pytest
from aiohttp import web
from lighter_gateway.config import GatewayConfig
from lighter_gateway.core import Gateway
from lighter_gateway.server import build_app

def _cfg():
    return GatewayConfig(upstream="https://up", host="0.0.0.0", port=8060,
                         rate_per_s=10.0, burst=10.0, max_stale_s=30.0,
                         cache_capacity=100, ttl={"/api/v1/orderBook": 2.5}, default_ttl=2.0)

@pytest.fixture
def gw():
    n = {"n": 0}
    async def fetch(method, path, query):
        n["n"] += 1
        return (200, b'{"mid": 1.23}', "application/json")
    g = Gateway(_cfg(), fetch)
    g._test_counter = n
    return g

async def test_health(aiohttp_client, gw):
    client = await aiohttp_client(build_app(gw))
    resp = await client.get("/__gw/health")
    assert resp.status == 200
    assert (await resp.json())["ok"] is True

async def test_proxy_and_cache(aiohttp_client, gw):
    client = await aiohttp_client(build_app(gw))
    r1 = await client.get("/api/v1/orderBook?market_id=2")
    assert r1.status == 200 and (await r1.json())["mid"] == 1.23
    r2 = await client.get("/api/v1/orderBook?market_id=2")
    assert r2.status == 200
    assert gw._test_counter["n"] == 1            # second served from cache
    stats = await (await client.get("/__gw/stats")).json()
    assert stats["hits"] >= 1 and stats["upstream_calls"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd scripts/marketdata && python -m pytest tests/test_server.py -v`
Expected: FAIL (`ModuleNotFoundError: lighter_gateway.server`). (Add `pytest-aiohttp>=1.0` to requirements in Step 3.)

- [ ] **Step 3: Write minimal implementation**

Add `pytest-aiohttp>=1.0` to `scripts/marketdata/requirements.txt`.

```python
# scripts/marketdata/src/lighter_gateway/upstream.py
from __future__ import annotations
import aiohttp


def make_fetch(session: aiohttp.ClientSession, base_url: str):
    base = base_url.rstrip("/")
    async def fetch(method: str, path: str, query: str):
        url = base + path + (("?" + query) if query else "")
        async with session.request(method, url, allow_redirects=False) as resp:
            body = await resp.read()
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return (resp.status, body, ct)
    return fetch
```

```python
# scripts/marketdata/src/lighter_gateway/server.py
from __future__ import annotations
from aiohttp import web
from .core import Gateway


def build_app(gw: Gateway) -> web.Application:
    app = web.Application()

    async def health(_req):
        return web.json_response({"ok": True})

    async def stats(_req):
        return web.json_response(gw.stats())

    async def proxy(req: web.Request):
        path = req.path
        query = req.rel_url.query_string
        resp = await gw.handle(req.method, path, query)
        return web.Response(status=resp.status, body=resp.body,
                            content_type=resp.content_type.split(";")[0],
                            headers={"X-Gateway-Source": resp.source})

    app.router.add_get("/__gw/health", health)
    app.router.add_get("/__gw/stats", stats)
    app.router.add_route("*", "/{tail:.*}", proxy)
    return app
```

```python
# scripts/marketdata/run_gateway.py
from __future__ import annotations
import os
import aiohttp
from aiohttp import web
from lighter_gateway.config import load_config
from lighter_gateway.core import Gateway
from lighter_gateway.upstream import make_fetch
from lighter_gateway.server import build_app


async def _make_app() -> web.Application:
    cfg = load_config(os.environ.get("GATEWAY_CONFIG", "config.yaml"))
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    gw = Gateway(cfg, make_fetch(session, cfg.upstream))
    app = build_app(gw)
    app["_session"] = session
    async def _close(_app):
        await session.close()
    app.on_cleanup.append(_close)
    app["_cfg"] = cfg
    return app


def main():
    cfg = load_config(os.environ.get("GATEWAY_CONFIG", "config.yaml"))
    web.run_app(_make_app(), host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd scripts/marketdata && python -m pytest tests/ -v`
Expected: PASS (all suites green).

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/
git commit -m "feat(gateway): aiohttp server, upstream client, run entrypoint"
```

---

### Task 7: Dockerfile + compose + shared network + README

**Files:**
- Create: `scripts/marketdata/Dockerfile`
- Create: `scripts/marketdata/docker-compose.gateway.yml`
- Create: `scripts/marketdata/README.md`

**Interfaces:**
- Produces: a `lighter-gateway` service on external Docker network `lighter-net`, container port `8060`, `restart: always`, healthcheck on `/__gw/health`.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# scripts/marketdata/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY run_gateway.py config.yaml ./
ENV PYTHONPATH=/app/src GATEWAY_CONFIG=/app/config.yaml
EXPOSE 8060
HEALTHCHECK --interval=15s --timeout=4s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8060/__gw/health').status==200 else 1)"
CMD ["python", "run_gateway.py"]
```

- [ ] **Step 2: Write the compose file**

```yaml
# scripts/marketdata/docker-compose.gateway.yml
services:
  lighter-gateway:
    build: .
    container_name: lighter-gateway
    restart: always
    networks: [lighter-net]
    # No published ports: only other containers on lighter-net reach it.
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8060/__gw/health').status==200 else 1)"]
      interval: 15s
      timeout: 4s
      retries: 3

networks:
  lighter-net:
    external: true
```

- [ ] **Step 3: Write the README** (operational notes)

```markdown
# Lighter Market-Data Gateway

Shared caching + single-flight + rate-limited reverse-proxy for Lighter REST reads.
All paper bridges point `connection.host` at `http://lighter-gateway:8060` (Docker
network `lighter-net`) instead of hitting Lighter directly, so the box makes one
upstream call per unique (endpoint, coin) per TTL window.

## Why
4 bridges polling Lighter from one IP tripped the per-IP 429 limit (2026-06-24),
starving bar feeds so 9-EMA retests expired unfilled. This centralizes + caps egress.

## Deploy (srv1370094)
    docker network create lighter-net   # once (idempotent: ignore "already exists")
    cd /docker/lighter-gateway && docker compose -f docker-compose.gateway.yml up -d --build
    # then add `networks: [lighter-net]` to each bridge + set host -> http://lighter-gateway:8060

## Observe
    docker exec lighter-gateway python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8060/__gw/stats').read().decode())"

## Tune
Edit config.yaml TTLs / rate_limit and `docker compose ... up -d --build`.
Watch /__gw/stats: upstream_calls should track unique coins, hits should dominate.
```

- [ ] **Step 4: Build locally to verify the image**

Run: `cd scripts/marketdata && docker build -t lighter-gateway:test .`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add scripts/marketdata/Dockerfile scripts/marketdata/docker-compose.gateway.yml scripts/marketdata/README.md
git commit -m "feat(gateway): Dockerfile, compose on shared lighter-net, README"
```

---

### Task 8: Deploy gateway to VPS + verify healthy

**Files:** none (ops task).

- [ ] **Step 1: Create the shared network + sync gateway to the VPS**

```bash
ssh srv1370094 "docker network create lighter-net 2>/dev/null || echo 'net exists'"
rsync -az --delete scripts/marketdata/ srv1370094:/docker/lighter-gateway/
```

- [ ] **Step 2: Build + start the gateway**

```bash
ssh srv1370094 "cd /docker/lighter-gateway && docker compose -f docker-compose.gateway.yml up -d --build"
```

- [ ] **Step 3: Verify health + that it can reach Lighter through itself**

```bash
ssh srv1370094 "docker exec lighter-gateway python -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8060/__gw/health').read().decode())\""
```
Expected: `{"ok": true}`.

- [ ] **Step 4: Smoke a real upstream read through the gateway** (candles for SOL market_id=2; confirms TLS-to-upstream + caching path)

```bash
ssh srv1370094 "docker exec lighter-gateway python -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8060/api/v1/candlesticks?market_id=2&resolution=5m&count_back=2').status)\""
```
Expected: `200` (exact path/params validated against the SDK during Task 6; adjust if the SDK uses a different candle path — the gateway's `default` TTL still serves it either way).

- [ ] **Step 5: Commit** (no code; record the deploy in the plan checkbox only).

---

### Task 9: Cut apex over to the gateway + verify fills resume

**Files:**
- Modify: `scripts/apex/config.apex.yaml` (`connection.host`)
- Modify: `scripts/apex/docker-compose.apex.yml` (join `lighter-net`)
- Modify: `scripts/apex/deploy/bridge.compose.vps.yml` (join `lighter-net`)

**Interfaces:**
- Consumes: live `lighter-gateway` on `lighter-net` (Task 8).

- [ ] **Step 1: Point apex at the gateway**

In `scripts/apex/config.apex.yaml`, change:
```yaml
connection:
  host: "http://lighter-gateway:8060"
  initial_collateral_usdc: 3000
```

- [ ] **Step 2: Join apex to the shared network** (in BOTH compose files, the service block and a top-level `networks:`)

```yaml
    networks: [default, lighter-net]
# ...
networks:
  lighter-net:
    external: true
```

- [ ] **Step 3: Deploy apex**

```bash
rsync -az scripts/apex/config.apex.yaml scripts/apex/docker-compose.apex.yml srv1370094:/docker/apex-bridge/
# apply the same host change to the LIVE config on the VPS if it differs (secrets live only there)
ssh srv1370094 "cd /docker/apex-bridge && docker compose -f docker-compose.apex.yml up -d"
```

- [ ] **Step 4: Verify apex reads via the gateway + bars stay fresh**

```bash
ssh srv1370094 "docker logs apex-bridge --since 4m 2>&1 | grep -iE 'Host:|new bar|429|bootstrap' | tail -15"
ssh srv1370094 "docker exec lighter-gateway python -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8060/__gw/stats').read().decode())\""
```
Expected: apex log shows `Host: http://lighter-gateway:8060`, fresh `new bar` lines, **zero** 429s; gateway stats `upstream_calls` rising slowly, `hits` climbing.

- [ ] **Step 5: Commit**

```bash
git add scripts/apex/config.apex.yaml scripts/apex/docker-compose.apex.yml scripts/apex/deploy/bridge.compose.vps.yml
git commit -m "chore(apex): route Lighter reads through lighter-gateway (kills 429 storm)"
```

---

### Task 10: Cut reclaim, rebound, scalper over to the gateway

**Files:**
- Modify: `scripts/reclaim-bridge/config.reclaim.yaml`
- Modify: `scripts/boost-bridge/config.rebound.yaml`
- Modify: `scripts/scalper-bridge/config.scalper.yaml`
- Modify: each bridge's `docker-compose.*.yml` to join `lighter-net`

**Interfaces:**
- Consumes: live `lighter-gateway` (Task 8). Same one-line host change as apex.

- [ ] **Step 1: Point each bridge's `connection.host` at the gateway**

In each config file set:
```yaml
connection:
  host: "http://lighter-gateway:8060"
```
(Keep every other field — collateral, symbols, secrets — untouched.)

- [ ] **Step 2: Join each bridge container to `lighter-net`** (service `networks:` + top-level external network block, exactly as Task 9 Step 2) in each bridge's compose file (and the VPS deploy compose if separate).

- [ ] **Step 3: Deploy each, one at a time, verifying between**

```bash
# reclaim
rsync -az scripts/reclaim-bridge/config.reclaim.yaml scripts/reclaim-bridge/docker-compose*.yml srv1370094:/docker/reclaim-bridge/
ssh srv1370094 "cd /docker/reclaim-bridge && docker compose up -d && sleep 5 && docker logs reclaim-bridge --since 3m 2>&1 | grep -ciE '429'"
# rebound
rsync -az scripts/boost-bridge/config.rebound.yaml scripts/boost-bridge/docker-compose*.yml srv1370094:/docker/boost-testnet/
ssh srv1370094 "cd /docker/boost-testnet && docker compose up -d && sleep 5 && docker logs rebound-bridge --since 3m 2>&1 | grep -ciE '429'"
# scalper
rsync -az scripts/scalper-bridge/config.scalper.yaml scripts/scalper-bridge/docker-compose*.yml srv1370094:/docker/scalper-paper/
ssh srv1370094 "cd /docker/scalper-paper && docker compose up -d && sleep 5 && docker logs scalper-bridge --since 3m 2>&1 | grep -ciE '429'"
```
Expected: each `grep -c 429` trends toward `0` after cutover.

- [ ] **Step 4: Confirm all four bridges now read through the gateway**

```bash
for c in apex-bridge reclaim-bridge rebound-bridge scalper-bridge; do echo "== $c =="; ssh srv1370094 "docker logs $c --since 2m 2>&1 | grep -i 'Host:' | tail -1"; done
```
Expected: every line shows `http://lighter-gateway:8060`.

- [ ] **Step 5: Commit**

```bash
git add scripts/reclaim-bridge/config.reclaim.yaml scripts/boost-bridge/config.rebound.yaml scripts/scalper-bridge/config.scalper.yaml scripts/*/docker-compose*.yml
git commit -m "chore(bridges): route reclaim/rebound/scalper Lighter reads through lighter-gateway"
```

---

### Task 11: Code cleanup — reflect the gateway in the shared feed code

**Files:**
- Modify: `scripts/apex/src/apex_bridge/feed_util.py` + the matching `bar_feed.py` docstring
- Modify: `scripts/reclaim-bridge/src/lighter_bridge/feed_util.py`
- Modify: `scripts/scalper-bridge/src/lighter_bridge/feed_util.py`
- Modify: `scripts/boost-bridge/src/lighter_bridge/feed_util.py`
- Test: existing per-bridge tests (`test_bar_feed_backoff.py`) must still pass.

**Rationale:** The gateway now owns rate-limiting centrally. The per-bridge WAF
exponential backoff stays as a **safety net** (it still fires if the gateway is
down and a bridge falls back to direct upstream), but its docstrings/comments must
no longer claim the bridge is the primary rate defender — that's misleading after
this change. We keep behavior, fix the narrative, and note the gateway. No logic
ripped out (YAGNI cuts the other way here: the backoff is cheap insurance).

- [ ] **Step 1: Run the existing feed tests to capture the green baseline**

Run: `cd scripts/apex && PYTHONPATH=src python -m pytest tests/test_bar_feed_backoff.py -v`
Expected: PASS (baseline before doc edits).

- [ ] **Step 2: Update the `feed_util.py` module docstring** (all four copies — identical text) to reflect the gateway

Replace the top docstring of each `feed_util.py` with:
```python
"""Pure helpers for the bar feed (no SDK imports -> unit-testable in isolation).

Rate-limiting is now centralized in the shared lighter-gateway (one caching,
rate-capped egress per VPS; see scripts/marketdata). These per-bridge backoffs
remain as a SAFETY NET for the degraded path where the gateway is unreachable and
the bridge falls back to hitting Lighter directly. WAF/captcha challenges still get
a long exponential backoff; ordinary transient errors get a short one.
"""
```

- [ ] **Step 3: Update the `bar_feed.py` "Design notes" comment** (all four copies) — change the line that says it polls Lighter directly to:
```python
#   - Polls REST `/candles` via the shared lighter-gateway (cached + rate-capped),
#     not Lighter directly. 5m bars + 30s poll = the gateway serves most reads
#     from cache; only one upstream call per coin per TTL window leaves the box.
```

- [ ] **Step 4: Re-run the feed tests for every bridge** (docstring-only change must not break anything)

Run:
```bash
cd scripts/apex && PYTHONPATH=src python -m pytest tests/ -q
cd ../reclaim-bridge && PYTHONPATH=src python -m pytest tests/ -q
cd ../scalper-bridge && PYTHONPATH=src python -m pytest tests/ -q
cd ../boost-bridge && PYTHONPATH=src python -m pytest tests/ -q
```
Expected: all PASS (unchanged counts).

- [ ] **Step 5: Commit**

```bash
git add scripts/apex/src/apex_bridge/feed_util.py scripts/apex/src/apex_bridge/bar_feed.py scripts/reclaim-bridge/src/lighter_bridge/feed_util.py scripts/reclaim-bridge/src/lighter_bridge/bar_feed.py scripts/scalper-bridge/src/lighter_bridge/feed_util.py scripts/scalper-bridge/src/lighter_bridge/bar_feed.py scripts/boost-bridge/src/lighter_bridge/feed_util.py scripts/boost-bridge/src/lighter_bridge/bar_feed.py
git commit -m "docs(bridges): note gateway owns rate-limiting; per-bridge backoff is now the fallback safety net"
```

---

### Task 12: 429 verification — confirm the storm is gone

**Files:** none (acceptance task; the test Rich asked for).

- [ ] **Step 1: Baseline window (record before claiming success)**

```bash
ssh srv1370094 "date -u"
for c in apex-bridge reclaim-bridge rebound-bridge scalper-bridge; do echo -n "$c: "; ssh srv1370094 "docker logs $c --since 30m 2>&1 | grep -c 429"; done
```

- [ ] **Step 2: Let it run ~30 min post-cutover, then re-measure the SAME window**

```bash
for c in apex-bridge reclaim-bridge rebound-bridge scalper-bridge; do echo -n "$c: "; ssh srv1370094 "docker logs $c --since 30m 2>&1 | grep -c 429"; done
```
Expected: total 429s across all four bridges ≈ 0 (down from ~170/30min during the storm).

- [ ] **Step 3: Confirm the gateway held upstream rate under the cap**

```bash
ssh srv1370094 "docker exec lighter-gateway python -c \"import urllib.request as u; print(u.urlopen('http://127.0.0.1:8060/__gw/stats').read().decode())\""
```
Expected: `upstream_non2xx` ≈ 0, `hits` >> `misses`, `throttled` low/zero.

- [ ] **Step 4: Confirm Apex specifically is healthy** — bars fresh for all 3 coins (incl. ZEC, previously starved), signals no longer expiring on feed starvation.

```bash
ssh srv1370094 "docker logs apex-bridge --since 30m 2>&1 | grep -iE 'ZEC.*new bar|expired|FILLED' | tail -20"
```
Expected: ZEC bars landing regularly; expiries (if any) are genuine no-retest, not feed gaps.

- [ ] **Step 5: Update memory + mark complete**

Update the Apex memory note: gateway deployed, 429 storm resolved, all four bridges routed through `lighter-gateway` on `lighter-net`. Record final 429 before/after numbers.

---

## Self-Review

**Spec coverage:** proxy/cache/single-flight/rate-limit/health-stats → Tasks 2–6; config → Task 1; Docker + shared network → Task 7; deploy + per-bridge cutover (all four) → Tasks 8–10; fallback safety net + cleanup → Task 11; 429 acceptance test → Task 12. Docker-network correction (vs the spec's `127.0.0.1`) is captured in Global Constraints + Task 7/9. All spec sections covered.

**Placeholder scan:** No TBD/TODO. The one runtime unknown — exact Lighter candle/order-book URL paths — is handled by the generic proxy + `default` TTL and validated in Task 6/8; not a placeholder.

**Type consistency:** `FetchFn = (method, path, query)->(status, body, content_type)` consistent across Tasks 5–6. `GatewayResponse.source` values consistent. `ResponseCache.get/put`, `TokenBucket.try_acquire`, `SingleFlight.do`, `Gateway.handle/stats` names match across tasks.
