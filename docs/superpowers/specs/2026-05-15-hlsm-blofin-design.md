# Plan · HLSM Memecoin Auto-Trader (BloFin MVP, Lighter-ready)

## Brief
Autonomous service that watches verified-skilled Hyperliquid perp wallets, fires when 3 or more converge on the same memecoin side within 45 minutes, and auto-executes paper-trade orders through a venue-pluggable executor (BloFin adapter at MVP, Lighter DEX adapter in V2). Built so paper to live is a one-variable `.env` flip with no code change. Single-page dashboard, sparse Telegram, full off-switch surface, isolated from the existing Scalp V3.1 bridge.

## Stack
- Python 3.12 (signal pipeline, scorer, executor, Telegram bot)
- FastAPI (dashboard backend, Telegram webhook receiver, internal API)
- PostgreSQL 16 (wallets, fills, positions, scores_history, signals, events)
- Redis 7 (WS fan-out, rate-limit token bucket, hot-reload pub/sub)
- hyperliquid-python-sdk (HL REST + WS, free public API)
- ccxt 4.x (BloFin demo + live REST, manual URL swap pattern)
- lighter-python SDK (V2 venue plug-in; not imported in MVP, but interface is shaped for it)
- Next.js 15 + Tailwind (dashboard, mirrors runner-intel pattern)
- python-telegram-bot 21.x (slash commands + alerts)
- Alembic (Postgres migrations)
- Docker Compose (deploy under /docker/hlsm-bridge/)
- Traefik file-provider (TLS + hlsm.agentneo.cloud routing, existing VPS pattern)
- pytest (unit + integration, ≥30 tests for MVP)
- Paid APIs: NONE. Hyperliquid is free and public. Replaces Apify spend, not adds to it.

## Scope
**Visuals**
- Top status bar: ARMED / PAUSED indicator, 7d paper equity sparkline, day PnL, open position count
- Live convergence feed: last 20 events with coin, side, wallet count, time, position state
- Open positions table: coin, side, entry, mark, PnL %, age, SL, TP, exit reason if closing
- Top-50 ranked wallets table: address (truncated), composite score, 90d PnL, sample size, last-active, sortable
- Per-coin drill-in modal: contributing wallets, convergence timeline, live position state, equity curve
- Single dark page, no infinite scroll, no log walls

**Functionality**
- Hyperliquid historical ingest: 90d fills per seed wallet, paginated, idempotent on re-run
- Daily leaderboard crawler: refresh candidate wallet set from HL public leaderboard
- Live WebSocket monitor: top-100 ranked wallets, position-change events ≤10s of HL block
- Trade reconstructor: fills to position timeline to per-trade PnL
- Stats calculator: Sharpe proxy, max drawdown, win rate, avg R:R, avg hold time, sample size, recency
- Composite scorer 0-100, anti-fluke filters (50+ trades, 30+ days active, no single trade > 50% PnL)
- Convergence detector: ≥3 ranked wallets, same side, same coin, within 45m, score floor 75
- Venue abstraction: `Exchange` interface with `place_order`, `attach_sl_tp`, `close_position`, `get_position`, `list_perps`, `get_balance`. BloFin adapter shipped in MVP. Lighter adapter is V2 plug-in via the same interface, no core-pipeline changes.
- Universe auto-discovery at startup: intersection of (HL memecoin perps) and (active venue's memecoin perps), via `Exchange.list_perps()`
- Executor: places venue order on convergence, attaches hard SL -25%, TP from observed wallet expectancy
- Per-trade sizing locked: $50 margin × 10x leverage = $500 notional per paper trade (tunable in weights.yaml; clamped per venue's min lot size)
- Exit policy: closes when any of {hard SL hit, TP hit, ≥2 of the 3 converged wallets have exited (median rule)} triggers first; owned entirely by hlsm-bridge service
- Paper / live mode controlled by single `.env` variable per venue (e.g. `HLSM_BLOFIN_ENV=demo|live`); code path identical, only API keys + base URL differ
- Daily-loss circuit breaker: cumulative day PnL ≤ -$100 auto-pauses system and alerts Telegram; manual `/hlsm resume` to re-arm
- Off-switches:
  - Telegram `/hlsm pause` halts new entries
  - Telegram `/hlsm drain` closes opens + halts
  - Telegram `/hlsm pause <COIN>` halts one coin
  - weights.yaml hot-reload picked up ≤30s, no restart
  - Daily-loss circuit breaker (default -$100 day PnL), auto-pause + alert
- Telegram alerts (sparse, signal-only): convergence, position open, position close with PnL, breaker trip, daily summary, daily heartbeat
- Dashboard live at https://hlsm.agentneo.cloud via existing Traefik
- Docker container `hlsm-bridge` with `restart: unless-stopped`

## Out of Scope
- Live trading with real money at launch (code path supports it via `.env` flip; activation requires separate Rich approval and runs through the same flip procedure proven on the existing blofin-bridge)
- Lighter DEX adapter implementation (V2 deliverable; MVP only locks the interface shape so the V2 plug-in is mechanical, not a refactor)
- Any modification of the existing blofin-bridge container or its DB (Scalp V3.1 isolation, absolute)
- Memecoin spot trading (perps only)
- Non-Hyperliquid signal sources (no Twitter, no GMGN, no Nansen, no Apify)
- Multi-user, SaaS auth, payment flow
- ML or AI predictive models (this is ranking + filtering + convergence, not forecasting)
- Confluence integration into other Leverage strategies (HLSM stays standalone in MVP)
- Scrapping SMC or Runner containers, cancelling Apify (separate teardown gate after HLSM is operational)

## Constraints
- Must NOT import from, modify, share state with, or rely on the `blofin-bridge` container
- Separate Docker network and bind mount at /docker/hlsm-bridge/
- Separate BloFin demo + live API keys, separate Telegram bot token, separate Postgres DB
- BloFin demo runs on its OWN sub-account (`Trials`), distinct from Scalp V3.1's BloFin demo account, so margin / positions / PnL / rate-limit budget do not entangle
- Demo API credentials are stored locally at `C:\Users\rakai\.hlsm-bridge-secrets\.env.demo` (outside the git repo) and scp'd to `/docker/hlsm-bridge/.env` at deploy time
- Post-deploy step: rotate the demo API key on BloFin after first successful end-to-end paper trade, since the initial key was transmitted in a chat session
- Demo to live is a single `.env` variable flip per venue; identical code path; verified by integration test in DoD
- Venue layer is an interface, not a hardcoded BloFin coupling; adding Lighter (or any ccxt-compatible venue) is a new adapter class, not a rewrite
- All tunables in weights.yaml, never hardcoded (including cluster_n, window_minutes, score_floor, per_trade_margin_usdt, leverage, daily_loss_pause_usdt, hard_sl_pct, exit_rule)
- Idempotent ingest: re-running produces zero duplicates
- "Bridge owns exits" within hlsm-bridge: no external service can close positions
- "No cheap fixes": full design, instrumentation, ≥30 tests; no band-aids
- Marginal infra cost ≤$30/mo
- Single source of truth for state is the Postgres DB; dashboard reads it, executor writes it, Redis only caches

## Definition of Done
The `hlsm-bridge` container runs on VPS 46.202.146.30 paper-trading BloFin memecoin perps autonomously from Hyperliquid wallet-convergence signals (N=3, 45m, score≥75) using a venue-pluggable executor (BloFin adapter live; demo↔live verified as `.env`-only flip; Lighter interface stub passes a no-op contract test), with $50×10x sizing, median (2-of-3) exit rule, -$100 daily-loss circuit-breaker, all 5 off-switches integration-tested, dashboard live at hlsm.agentneo.cloud, sparse Telegram alerts firing, and an end-to-end test passing where a synthetic convergence event produces a real BloFin demo order with SL/TP attached, a `positions` DB row written, and a Telegram message sent.

## Acceptance Criteria
- 90d historical fills ingested for top 100 HL leaderboard wallets; `SELECT count(*) FROM fills WHERE wallet=:addr` returns a sensible number per wallet; re-running ingest produces zero duplicate rows
- Live WS monitor active on top-100 ranked wallets; position-change events written to `events` table with median latency ≤10s from HL block time
- Convergence detector fires on N=3, window=45m, score floor=75; replaying 90d history against the target universe produces a non-empty `signals` history with each row reproducible from raw fills
- BloFin demo executor opens a paper position within 30s of convergence detection, sized at $50 margin × 10x leverage = $500 notional, in the auto-discovered universe of memecoin perps available on BloFin demo, with hard SL (-25%) and TP both attached
- Median exit rule fires: when ≥2 of the 3 originally-converged wallets have closed their HL position (or flipped side), our paper position closes; verified by replay test on historical convergence events
- All 5 off-switches operational and pass integration tests: `/hlsm pause` halts new entries, `/hlsm drain` closes opens + halts, `/hlsm pause <COIN>` halts one coin, weights.yaml change picked up ≤30s, daily-PnL breaker at -$100 auto-pauses + alerts
- Venue interface in place: `Exchange` abstract class with full method set, BloFin adapter implements it cleanly, Lighter stub adapter passes contract test that asserts the interface is satisfiable (no real network calls)
- Demo-to-live flip verified: integration test flips `HLSM_BLOFIN_ENV` from `demo` to `live`, container picks up new credentials on restart, hits live URL with a balance-read call (read-only, no order placed), and reverts to demo cleanly
- Dashboard live at https://hlsm.agentneo.cloud, rendering status bar, convergence feed (last 20), open positions table, top-50 wallets, per-coin drill-in modal, with zero browser console errors
- Telegram alerts fire only on: convergence detected, position opened, position closed with realized PnL, breaker tripped, daily summary, daily heartbeat. Zero per-wallet noise.
- Container `hlsm-bridge` deployed under /docker/hlsm-bridge/ with `restart: unless-stopped`; daily heartbeat message delivered for 3 consecutive days
- ≥30 pytest tests passing, covering ingest idempotency, trade reconstruction math, stats calculator, convergence rule, executor sizing + SL/TP attach, all 5 off-switches, end-to-end pipeline
- End-to-end test: inject synthetic convergence event into pipeline; assert BloFin demo order placed (verify via demo API), SL and TP attached, `positions` row has all required columns populated, `signals` row linked to position, Telegram message sent to test channel

## Verification
- `ssh root@46.202.146.30 "docker ps --filter name=hlsm-bridge --format '{{.Status}}'"` returns a running healthy status
- `ssh root@46.202.146.30 "docker exec hlsm-bridge pytest -q"` exits 0 with ≥30 tests
- `curl -s https://hlsm.agentneo.cloud/api/health` returns JSON with `status=ok` and `armed` boolean
- `curl -s https://hlsm.agentneo.cloud/api/stats` returns JSON: tracked_wallets, scored_wallets, convergence_events_24h, open_positions, day_pnl_usdt
- Open https://hlsm.agentneo.cloud in browser: status bar, convergence feed, positions table, wallet leaderboard render with live data, drill-in modal opens on click, no console errors
- Telegram bot: send `/hlsm pause`, dashboard ARMED flips to PAUSED in ≤5s; send `/hlsm resume`, returns ARMED
- Telegram bot: with ≥1 open paper position, send `/hlsm drain`, all positions close in DB and BloFin demo, state stays PAUSED
- Edit weights.yaml on VPS (change cluster_n from 3 to 4), confirm dashboard reflects new value within 30s, no container restart in `docker logs --since 1m`
- SQL: `SELECT close_reason, count(*) FROM positions WHERE status='closed' GROUP BY 1` produces only values in {sl, tp, wallet_exit, breaker, drain}, with no nulls
- SQL: `SELECT count(*) FROM signals s LEFT JOIN positions p ON p.signal_id=s.id WHERE p.id IS NULL AND s.status='filled'` returns 0 (every filled signal has a linked position)

## Turn Budget
Stop after 80 turns, or sooner once DoD condition holds.

## References
- Parked HLSM spec set: `C:\Users\rakai\Leverage\docs\hlsm\` (SPEC.md, PLAN.md, PROFIT_MECHANICS.md, DECISIONS.md)
- Existing blofin-bridge ops memory: demo↔live flip procedure, separate BLOFIN_DEMO_* keys, manual URL swap for ccxt sandbox (NOT `set_sandbox_mode`)
- Pattern donors: smc-trading docker-compose layout, runner-intel weights.yaml hot-reload + dashboard structure, Traefik file-provider config at /docker/traefik-mncm/
- Hyperliquid API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- VPS layout memory: services map for 46.202.146.30, Traefik routing pattern
- Profitability priority memory: no cheap fixes, profitable trading is the #1 directive
- Bridge-owns-exits feedback memory: signal providers only OPEN setups, exit logic stays in the executing service

## Risks / Open Questions
- HL wallet alpha is luck not skill: mitigated by anti-fluke filters (50+ trades, 30+ days, no single-trade dominance) + recency-weighted scoring; failure flag if <20 wallets clear scoring floor
- Convergence too rare on memecoin perps: mitigation is tunable params; fallback is expand universe to memecoins + majors if 7d signal count <5
- BloFin demo doesn't list all target memecoin perps: confirm at startup via `Exchange.list_perps()`, fall back to listed subset, log gaps to dashboard
- Hyperliquid API rate-limit or policy change: aggressive 90d backfill on day-1, Redis token bucket, alert on rate-limit hits
- Off-switch fails when most needed: each switch has its own integration test in DoD; daily heartbeat doubles as canary
- Lighter memecoin coverage may be thinner than BloFin: V2 adapter rollout includes a coverage report comparing Lighter's perp list to current trading universe; system can run a venue per coin if needed
- Demo-to-live flip risks: live API behaviour can diverge from demo (rate limits, lot sizes, slippage). Live activation gated by Rich, includes a read-only smoke test first, then a single $50 margin trade as canary before full arming.

---

**/goal one-liner**:

```
/goal hlsm-bridge container runs autonomously on VPS 46.202.146.30 paper-trading BloFin memecoin perps from Hyperliquid wallet-convergence signals (N=3, 45m, score>=75) at $50x10x sizing with 2-of-3 median exit and -$100 daily breaker, through a venue-pluggable executor where demo<->live is a .env-only flip and the Lighter adapter is a stub passing a contract test, with all 5 off-switches integration-tested, dashboard live at hlsm.agentneo.cloud, sparse Telegram alerts firing, and end-to-end synthetic-convergence test producing a real BloFin demo order with SL/TP attached, a positions DB row, and a Telegram message, stop after 80 turns
```
