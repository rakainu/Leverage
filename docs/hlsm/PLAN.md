# HLSM — Build Plan

Status legend: `[ ]` not started, `[~]` in progress, `[x]` done, `[!]` blocked.

---

## First build task (start here when resuming)

**Build the Hyperliquid historical ingestor. Nothing else first.**

Specifically: a Python script that, given a list of wallet addresses, pulls the last 90 days of fills via `api.hyperliquid.xyz/info` endpoint `userFills`, persists them to a fresh PostgreSQL `fills` table with a proper schema (`ts, wallet, coin, side, px, sz, dir, start_position, hash, fee`), handles pagination + rate limits, and is idempotent on re-run.

Seed with top 20 wallets from the current Hyperliquid leaderboard (manual copy-paste — scraper comes later).

**Definition of done:**
- [ ] `SELECT count(*) FROM fills WHERE wallet = '<addr>';` returns a sensible number per seed wallet
- [ ] Re-running the script produces zero duplicates
- [ ] Spot-check 5 wallets: sorted-by-ts fills reconstruct into sensible position timelines
- [ ] Script logs rate-limit budget consumption per run

Time budget: one focused evening.

---

## MVP (Weeks 1–3)

**Objective:** Daily ranked list of Hyperliquid traders with risk-adjusted scores, real-time feed of their position changes, viewable in a simple dashboard. No execution yet.

### Ingestion
- [ ] Historical ingestor (first build task above)
- [ ] Live WebSocket monitor for top-100 wallets (position + fill subscriptions)
- [ ] Leaderboard crawler (daily refresh of candidate wallets)
- [ ] Rate-limit tracker + Redis token bucket

### Enrichment
- [ ] Trade reconstructor: fills → position timeline → per-trade PnL
- [ ] Stats calculator: Sharpe proxy, max DD, win rate, avg R:R, avg hold time, sample size, recency
- [ ] Style classifier v1: scalper / swing / positional based on avg hold time + turnover

### Scoring
- [ ] Composite scorer v1 (weighted 0–100)
- [ ] Anti-fluke filter: min 50 trades, min 30 days active, no single trade > 50% of total PnL
- [ ] Score snapshot daily to `scores_history`

### Storage
- [ ] Postgres schema: `wallets, fills, positions, events, scores_history`
- [ ] Migrations committed to repo

### Backend
- [ ] FastAPI scaffold
- [ ] `/api/wallets` ranked list with filters
- [ ] `/api/wallet/:addr` detail + equity curve
- [ ] `/api/events` live event feed

### Frontend
- [ ] Next.js scaffold (copy pattern from Runner dashboard)
- [ ] `/ranked` wallet leaderboard page
- [ ] `/wallet/:addr` detail page
- [ ] `/live` real-time event feed

### Alerts
- [ ] Telegram bot: new position open by any wallet scored ≥ 80

### Tests
- [ ] pytest scaffold
- [ ] Unit tests: trade reconstruction, stats math, scorer
- [ ] Integration test: ingest → reconstruct → score pipeline on a fixture wallet

### Ops
- [ ] Dockerfile + docker-compose.yml
- [ ] Deploy to existing VPS under `/docker/hlsm/`
- [ ] Daily Telegram heartbeat

---

## V2 (Weeks 4–8)

**Objective:** Validate edge empirically, introduce aggregate signals, build backtest engine.

- [ ] Backtest engine: replay signal rules over 90d, measure forward returns at 1h/4h/24h
- [ ] Aggregate positioning index per asset (net long/short among top-100, 5m buckets)
- [ ] Aggregate-shift detector with configurable Δ thresholds
- [ ] Coordinated-flow detector (N distinct wallets, same side, within Z min)
- [ ] Style classifier v2: adds leverage usage, volatility of holdings, asset preference, time-of-day bias
- [ ] Signal decay curves per wallet class (how long does alpha persist?)
- [ ] Signal quality dashboard: hit rate, expectancy, regime conditioning per signal type
- [ ] Out-of-sample validation holdout (hold last 30 days from scorer training)

**Validation milestone:** A signal rule with Sharpe > 1.5 on 90d backtest, ≥100 trades, robust to ±20% parameter jitter.

---

## V3 (Weeks 9–14)

**Objective:** Close the loop. Signals feed the Leverage bridge with risk controls.

- [ ] Signal dispatcher: queryable HLSM state endpoint bridge can hit at alert-time
- [ ] Verdict API: `aligned | neutral | conflicting | strong_confluence` + optional size modifier
- [ ] Bridge integration on the Leverage side (separate repo change)
- [ ] Confluence mode: HLSM + TV within N min → size up; HLSM-only → size down; conflict → skip
- [ ] Risk governor: per-signal sizing based on historical expectancy + current regime
- [ ] Regime classifier (BTC trend/chop/vol) gates which signal archetypes fire
- [ ] Execution PnL attribution: every bridge trade tagged with source (TV-only / HLSM-only / confluence)
- [ ] Daily auto-report: PnL by source, hit rate, wallet contribution leaderboard
- [ ] Wallet decay monitor: auto-flag wallets with 30d score drop > 25% vs 90d

---

## Validation plan (what proves it works)

**Primary metric:** Forward-return Sharpe of top-decile signal rule over 90d replay > 1.5, with ≥100 trades, robust to ±20% parameter jitter.

**Secondary metrics:**
- Hit rate of "top-scored wallet opens" at 4h horizon > 55%
- Aggregate positioning index correlation with 6h forward BTC/ETH returns: |ρ| > 0.25
- Per-wallet alpha decay window > 30 min (actionable via BloFin)
- Coverage: ≥40 tradeable assets with ≥10 scored wallets each

**Early positive signs (weeks 2–4):**
- Historical ingest completes clean (>99% fills reconstructed)
- Top-ranked wallets "look like" skilled traders on manual equity-curve inspection
- Scored wallets' forward PnL on 30d holdout tracks their score ordering
- At least one coordinated-flow event visually clearly precedes a move

**Failure conditions:**
- Backtest Sharpe stays < 1.0 after V2 iteration, or collapses under parameter jitter
- Signal decay < 60s (BloFin can't execute in time)
- Top wallets turn out to be structurally wash/spoof/self-filling
- < 20 wallets survive anti-fluke filters

**Justifies continuing past V2 if:** any one of decent Sharpe (>1.2), strong aggregate correlation, or reliable coordinated-flow detection. Only one of three mechanisms needs to work.

---

## Risks (ranked)

1. **Hyperliquid API policy change** (medium-term likely). Mitigation: aggressive historical backfill now, tolerate reduced refresh later.
2. **Top-wallet alpha is luck not skill.** Mitigation: anti-fluke filters, OOS scoring, style classification.
3. **Signal decay faster than BloFin execution latency.** Mitigation: measure decay in V2 before committing V3 execution; consider HL-native fallback only if necessary.
4. **Scorer overfits.** Mitigation: 30d holdout, parameter jitter tests, keep v1 simple.
5. **Wallet style drift.** Mitigation: rolling re-classification, auto-demote on score decay.
6. **Competing product ships.** Mitigation: edge is execution integration, not the dashboard — don't get distracted by productizing.
7. **VPS/pipeline outage.** Mitigation: healthcheck + restart + Telegram heartbeat.
8. **Scope creep into "Coinglass competitor".** Mitigation: frozen scope in SPEC.md; new ideas go to a V4 parking lot.
