# Lighter Market-Data Gateway — Design

**Date:** 2026-06-24
**Status:** Approved (approach), spec under review
**Author:** Claude + Rich

## Problem

All Lighter paper bridges on srv1370094 (apex, reclaim, rebound, scalper) poll
Lighter's REST API independently from the **same egress IP**:

- **Order-book snapshot** per coin every `mark_poll_interval_s` (3s), round-robin,
  for *every enabled coin even when flat* — ~1 req/s per bridge just for marks.
- **5m candles** per coin every `bar_poll_interval_s` (30s).

Aggregate across 4 bridges with overlapping coin sets is ~8–9 read req/s sustained
from one IP, which trips Lighter's per-IP rate limit. Result: HTTP 429 storms
(measured ~170 rejections / 30 min), the bar feed backs off (apex ZEC reached
consecutive-error #11, ~3.5-min gaps), the 9-EMA retest cannot be evaluated, and
**every queued Apex signal expires unfilled** (`trade_log` = 0, blank dashboard).

The root cause is **structural duplication**: N bridges fetch the same data from
the same IP. Reducing poll rates or pausing bridges only delays recurrence. The
permanent fix removes the duplication.

## Goal

A shared, box-local gateway so the VPS makes **one upstream Lighter call per unique
(endpoint, coin) per cache window**, regardless of how many bridges run — with a
hard rate cap that makes exceeding Lighter's limit structurally impossible. Each
bridge changes by **one line** (its `connection.host`). Fixes apex, reclaim,
rebound, and scalper transparently.

Non-goal: changing any strategy logic, entry/exit behavior, sizing, or signals.
Marks/bars delivered to bridges must be as fresh as today (≤ ~3s for order books).

## Approach (chosen)

**Transparent caching + single-flight + rate-limited reverse-proxy** for Lighter
read endpoints, running as its own container on the VPS, bound to loopback.

- Bridges set `connection.host = http://127.0.0.1:<gw_port>` instead of
  `https://mainnet.zklighter.elliot.ai`. The Lighter SDK builds the same request
  paths; only the base host changes (`main.py:118`).
- The gateway forwards each request to the real upstream over HTTPS, **caches the
  response** keyed by `(method, path, query)` for a short per-endpoint TTL, and
  serves all bridges from cache within that window.
- **Single-flight (request coalescing):** concurrent identical requests share one
  in-flight upstream fetch — a thundering herd of 4 bridges asking for ZEC's order
  book at once becomes **one** upstream call.
- **Global token-bucket rate limiter** on upstream calls: a hard ceiling
  (configurable, default well under Lighter's limit) the box can never exceed.
  When the bucket is empty, the gateway serves the last cached value (stale-OK for
  marks) rather than hammering upstream.

Why this over a bespoke market-data API: minimal, low-risk cutover on **live**
bridges (one config line each, no data-layer rewrite), endpoint-agnostic (covers
candles, order book, market config, and any future read path automatically), and
it puts the rate cap at the egress where it actually belongs.

Why not WebSocket push: the codebase deliberately abandoned the Lighter order-book
WS after silent-death / reconnect-churn incidents (2026-05-22, 2026-05-23) in
favor of REST snapshots. This design keeps the proven REST path and does not
reintroduce that failure class.

## Architecture

```
            ┌─────────────────────────── srv1370094 ───────────────────────────┐
            │                                                                   │
 apex ─┐    │   connection.host = http://127.0.0.1:8060                         │
reclaim┤    │        │                                                          │
rebound┤────┼────────┼──────────►  lighter-gateway (container, loopback :8060)  │
scalper┘    │        │              • single-flight coalescing                  │
            │        │              • per-path TTL cache (LRU)                  │
            │        │              • global token-bucket rate limiter          │
            │        │                      │                                   │
            └────────┼──────────────────────┼───────────────────────────────────┘
                     │                       ▼
                     │             https://mainnet.zklighter.elliot.ai
                     ▼
              (fallback: if gateway down, bridge falls back to direct host)
```

### Component: `lighter-gateway`

A small async HTTP service (aiohttp). New package `scripts/marketdata/`.

Responsibilities (each independently testable):

1. **Proxy** — forward an inbound request to the configured upstream base URL,
   preserving path, query, method (GET only for reads), and relevant headers;
   return the upstream status/body/content-type verbatim on a cache miss.
2. **Cache** — an in-memory TTL+LRU store keyed by `(method, path, sorted-query)`.
   Per-path-prefix TTL config:
   - order-book endpoints: ~2.5s (matches today's 3s mark poll → no freshness loss)
   - candlestick endpoint: ~20s (5m bars; cheap freshness)
   - order-book/market-config metadata: long (minutes) — near-static
   - default for unmatched read paths: short (~2s), fail-safe.
3. **Single-flight** — a keyed in-flight map so concurrent identical misses await
   one upstream fetch.
4. **Rate limiter** — async token bucket on upstream calls (rate + burst from
   config). On exhaustion: serve last-known cached body if present (even if past
   TTL, capped at a `max_stale_s`); else return 503 so the bridge backs off.
5. **Health/metrics** — `/__gw/health` (liveness) and `/__gw/stats` (upstream
   call count, cache hit rate, 429s seen from upstream, rate-limit waits) for the
   429 verification test.

### Config (`scripts/marketdata/config.yaml`)

```yaml
upstream: "https://mainnet.zklighter.elliot.ai"
listen: { host: "127.0.0.1", port: 8060 }
rate_limit: { rate_per_s: 4.0, burst: 8 }      # hard upstream ceiling for the box
max_stale_s: 15                                 # serve cache past TTL up to this when throttled
ttl:
  "/api/v1/candlesticks": 20
  "/api/v1/orderBook": 2.5         # exact prefixes confirmed against the SDK at build time
  "/api/v1/orderBooks": 300        # market-config metadata
  default: 2.0
```

(Exact upstream paths are confirmed by inspecting the Lighter SDK's request URLs
during implementation; the prefix table is updated to match. The proxy is generic,
so an unmatched path still works via `default`.)

### Bridge change (per bridge, one line)

`config.*.yaml`: `connection.host: "http://127.0.0.1:8060"`.

**Fallback for the shared-failure-point risk:** the bridge treats the gateway as
its host but, on a connection error to the gateway (refused/timeout), retries the
request once against the real upstream host before applying its normal backoff.
Implemented as a tiny host-fallback wrapper in each bridge's client init (shared
helper, copied like the other per-bridge utilities). This guarantees the gateway
can never *permanently* strand a bridge; a gateway crash degrades to today's
behavior (direct polling) until `restart: always` brings it back.

## Data flow (Apex entry, end to end)

1. TV alert → `/webhook/apex` → signal queued (unchanged).
2. BarFeed requests candles from `http://127.0.0.1:8060/...` → gateway serves
   cached or single-flight upstream → fresh 5m bar.
3. On the new bar, 9-EMA retest + filters evaluated (unchanged logic) → entry.
4. Mark poll requests order-book snapshot from the gateway → fresh mid for
   sizing/fill and the trail state machine (unchanged logic).

Apex sees the same data shape and freshness as today, minus the 429 starvation.

## Error handling

- **Upstream 429/5xx:** gateway returns it to the bridge *only* on a cache miss
  with no usable stale value; otherwise serves stale (≤ `max_stale_s`). The global
  rate limiter is sized so upstream 429s should approach zero in steady state.
- **Upstream timeout:** same as 5xx; serve stale if available else 504.
- **Gateway down:** bridge host-fallback to direct upstream (degraded, today's
  behavior); `restart: always` recovers the container.
- **Cache poisoning:** only 2xx JSON responses are cached; non-2xx is never cached.

## Testing

Unit (pytest, no network — upstream mocked):
- cache hit within TTL serves cached body, **zero** extra upstream calls;
- single-flight: K concurrent identical misses → exactly **1** upstream call;
- rate limiter: bursts beyond `burst` are throttled; stale served when throttled;
- TTL expiry triggers exactly one refresh;
- non-2xx never cached; query-order-independent cache key.

Integration / acceptance (the test Rich asked for):
- Deploy gateway; cut **apex** over first; confirm Apex bars/marks stay fresh and a
  signal can fill.
- Cut over reclaim, rebound, scalper.
- Measure **429s / 30 min across all bridges** before vs after. Success = 429s
  drop to ~0 and `/__gw/stats` shows upstream call rate held under `rate_per_s`.

## Rollback

Per bridge: revert `connection.host` to the direct upstream and restart — instant,
independent. The gateway is additive; removing it returns to today's setup.

## Out of scope

Activity-gated polling (only fast-poll coins with open/pending positions) is a
further optimization that composes with this gateway but is **not** required to fix
the rate limit and is not included here. Strategy logic untouched.
