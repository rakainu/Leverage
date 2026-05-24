# Lighter Trading Dashboard — Design

**Date:** 2026-05-24
**Status:** Approved (design), pending implementation plan
**Owner:** Rich (rakainu)

## Purpose

Give Rich a pleasant, human-readable web view of the live Lighter paper-trading
bridge. Today the only windows into the bridge are SSH + raw `sqlite3` queries
and Telegram pings. This dashboard makes performance and live state visible at a
glance during desktop review sessions.

Scope is **Lighter only**. BloFin trades are viewable in BloFin's own paper UI
and are explicitly out of scope.

## Non-goals

- No order entry, no controls, no ability to mutate trades. **Read-only.**
- No BloFin data.
- No mobile-first layout (desktop analytics is the primary use case; the layout
  should still be usable on a phone but is not optimized for it).
- No re-implementation of V3.1 as a Freqtrade strategy. The bridge keeps its own
  Python/SQLite stack untouched (except the one WAL change below).

## Primary use case

Desktop analytics sessions — periodic deep-dives at the laptop to review
performance, study trades, and confirm the bridge is behaving. Data density and
charts are prioritized over mobile ergonomics.

## Architecture

A new standalone container `lighter-dashboard` on srv1370094 (`46.202.146.30`),
fully isolated from the trading bridge.

```
                    Browser (desktop)
                         │  HTTPS + basic auth
                         ▼
              Traefik (existing) ── lighter.<domain>
                         │
                         ▼
            lighter-dashboard  (FastAPI + Uvicorn, :8080 internal)
              ├─ reads  lighter_paper.db   (READ-ONLY bind mount, :ro)
              └─ fetches live marks from Lighter public REST
                         │
              (the bridge writes the DB; the dashboard only ever reads)
```

### Isolation guarantees

- The dashboard makes its **own** Lighter public REST calls for live marks. It
  never calls into the bridge process: no shared memory, no shared event loop,
  no IPC.
- If the dashboard crashes or hangs, the bridge is unaffected and keeps trading.
- The dashboard is a separate container with its own lifecycle; it can be
  restarted/redeployed without touching the bridge.

**Read-only enforcement (revised during planning):** WAL mode requires any
reader to write the `-wal`/`-shm` sidecar files, so a strict read-only (`:ro`)
filesystem mount is incompatible with WAL. Instead, the data directory is
mounted read-write and read-only is enforced at the SQLite **connection** level:
every dashboard connection runs `PRAGMA query_only = ON;` and the code only
issues `SELECT`. A `query_only` connection rejects any write with an error, so
the dashboard remains functionally unable to mutate trade data while keeping
WAL's lock-free concurrent reads.

### One bridge change: WAL mode

Enable WAL journal mode on the bridge's SQLite (`PRAGMA journal_mode=WAL` in
`scripts/lighter-bridge/src/lighter_bridge/db.py`). This lets a second reader
process (the dashboard) read without ever blocking the bridge's writes — the
textbook one-writer/many-readers pattern.

- The change is **strategy-neutral**: it affects only how SQLite persists
  writes, not entries, exits, sizing, or any signal logic. It does not pollute
  the 30-day eval's variable isolation (that rule governs strategy params).
- Requires one bridge restart to take effect. The bridge is already restart-safe
  via `restore_open_positions()`.

## Components

```
lighter-dashboard/
├─ app.py            # FastAPI app, routes, basic-auth dependency
├─ db.py             # read-only SQLite queries (one function per panel)
├─ marks.py          # Lighter REST client for live order-book mid (cached ~2s)
├─ stats.py          # derived metrics: PF, win%, drawdown, equity series
├─ templates/
│   ├─ index.html    # full page shell (loaded once)
│   └─ partials/     # htmx fragments: kpis, positions, equity, trades, …
├─ static/           # Tailwind output CSS + tiny chart JS
├─ Dockerfile
└─ docker-compose.yml
```

- **db.py** — opens the DB read-only, one query function per panel
  (`get_open_trades`, `get_closed`, `get_signals`, `get_snapshots`). No business
  logic.
- **marks.py** — fetches best bid/ask from Lighter public REST per symbol,
  computes mid, caches ~2s so rapid panel refreshes don't hammer the API.
  Independent of the bridge's mark logic but uses the same order-book-mid math.
- **stats.py** — pure functions: rows in, metrics out (PF, win-rate, max
  drawdown, equity time-series). Unit-testable with no DB or network.
- **app.py** — routes return either the full page or an htmx partial. Live
  panels self-refresh via htmx polling.

## Panels (approved wireframe)

| Panel | Content | Refresh |
|-------|---------|---------|
| A | KPI strip — equity, open count, realized 30d, profit factor, max drawdown | 3s |
| B | Equity curve — 7d / 30d / all-time toggle | 15s |
| C | Open positions (live) — entry, current mark, unrealized PnL, trail SL, state | 3s |
| D | Recent closed trades — last 20, all symbols | 15s |
| E | Exit-reason mix — SL / SL→BE / Trail / Manual counts + PnL | 15s |
| F | Per-symbol stats — n, win%, net, PF | 15s |
| G | Signal log — detected / fired / blocked / expired | 15s |

## Data flow

1. **Static data (panels B, D, E, F, G):** dashboard queries the read-only DB.
   Exact to last bridge write; refreshes every 15s.
2. **Live data (panels A, C):** dashboard queries the DB for open positions, then
   fetches current order-book mid from Lighter REST to compute unrealized PnL.
   Refreshes every 3s. Accuracy ~2–5s behind real (parity with bridge's 5s tick).
3. **Equity series:** combines `account_snapshot` rows (5-min cadence, written by
   the bridge) with live unrealized PnL for the leading edge of the curve.

## Tech stack

FastAPI + HTMX + Tailwind. One Python service, server-rendered HTML, HTMX
partial swaps for live panels. No Node, no SPA build step. Same Python ecosystem
as the bridge.

## Access & auth

- Public HTTPS via the existing Traefik reverse proxy + Let's Encrypt.
- Subdomain **`lighter.agentneo.cloud`** — one Cloudflare DNS record (Rich sets
  up when prompted) + a Traefik label.
- Traefik **basic-auth** middleware. Username `radk9`. Password stored only as a
  bcrypt/htpasswd hash on the VPS (in the dashboard's Traefik config / `.env`) —
  **never committed to the repo in plaintext**.

## Visual direction

- Dark theme (the approved wireframe look).
- **Text floor 14–16px minimum** — hard requirement for readability.
- Color semantics: green = profit/long, red = loss/short, amber = blocked/neutral.

## Error handling

- **Lighter REST unreachable:** live panels show the last cached mark with a
  "stale mark" indicator rather than erroring. Unrealized PnL falls back to the
  most recent `account_snapshot`.
- **DB locked/unavailable:** panels show "no data" gracefully; the page never
  crashes on a transient read.
- **Dashboard down:** no effect on the bridge — it is a fully separate container.

## Testing

- **stats.py:** unit tests for PF, win-rate, max-drawdown, equity-series math
  against fixture rows (including edge cases: zero trades, all-losses, single
  trade).
- **db.py:** tests against a fixture SQLite file mirroring the bridge schema.
- **marks.py:** test mid computation + cache behavior with a mocked REST client.
- **Manual:** load the dashboard against a copy of the live DB; verify each panel
  renders, live panels refresh, and numbers match raw `sqlite3` queries.

## Deployment

- New `lighter-dashboard` container in its own compose file on srv1370094.
- Read-only bind mount of `/docker/lighter-paper/data/lighter_paper.db`.
- Traefik labels for routing + basic-auth + TLS.
- The bridge's WAL change deployed via the existing bind-mount + restart flow.

## Open items (resolved)

- Domain: `lighter.agentneo.cloud`. Cloudflare DNS record added by Rich when the
  deploy step prompts for it.
- Basic-auth: username `radk9`; password hashed at deploy, stored on VPS only.
