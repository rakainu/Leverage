# Memecoin Runner Intelligence System — Design Spec

**Status:** Draft, awaiting user review
**Author:** Claude (with Rich)
**Date:** 2026-04-11
**Container name:** `runner-intel`
**Folder:** `meme-trading/runner/`

---

## 1. Purpose

Build a modular, second-generation memecoin detection and scoring system that identifies higher-quality runner candidates by combining:

- The existing wallet cluster signal (3 tracked wallets buying the same token within 30 minutes)
- A new filter + scoring pipeline (wallet quality, entry quality, holder quality, rug/risk, follow-through)
- Explainable verdicts (Ignore / Watch / Strong Candidate / Probable Runner)
- Paper trading with rich Telegram alerts so Rich can mirror trades manually on Phantom/Axiom/GMGN
- A dashboard for observability and configuration

The current deployed system (`smc-trading` at `meme.agentneo.cloud`) keeps running untouched as a comparison baseline.

## 2. Scope decisions (locked)

| Decision | Value | Rationale |
|---|---|---|
| **Relation to current system** | Option C (parallel rebuild), bounded at paper trading | Avoids rework; old bot keeps running as baseline; no capital risk |
| **Live trading** | Interface scaffolded, NOT implemented in v1 | Rich may never go live; may trade manually from Telegram signals |
| **Deployment** | Own Docker container (`runner-intel`), own SQLite DB, own docker-compose service | Clean boundary; independent restarts; no risk to live bot |
| **Wallet universe** | Shared `meme-trading/config/wallets.json` + private tier overlay (A/B/C) | Single source of truth for curation; tier logic is runner's opinion |
| **Cluster counting** | Only A-tier and B-tier wallets count toward convergence threshold | Per brief: "only allow A-tier or B-tier wallets to count" |
| **Data stack (no new paid APIs)** | Helius, RugCheck (free), DexScreener, Jupiter, Nansen, GMGN/Apify | $0 new spend; RugCheck's free insiders graph replaces Bitquery |
| **Scope of v1** | Full pipeline: ingest → cluster → filters → scoring → paper executor → dashboard → alerts | "Full scope" per user decision |
| **Dashboard in v1** | Yes, included | Confirmed by user |
| **Scoring tunability** | All weights/thresholds/gates live in `config/weights.yaml`, hot-reloadable | "Easy to tweak" is a non-negotiable design goal |
| **Commit discipline** | Commit meaningful units immediately, push to `rakainu/Leverage` | Standing rule from CLAUDE.md, reinforced by user |

## 3. Data sources

### Use (all free or already paid)

| Source | Purpose | Rate limit target |
|---|---|---|
| **Helius** (RPC + WS + DAS) | Wallet events, token metadata, holder lists, deployer history | 10 RPS |
| **RugCheck** (free, no auth) — `/v1/tokens/{mint}/report/summary` | Rug risk score, LP lock %, named risks, mint/freeze authority | 2 RPS |
| **RugCheck** (free, no auth) — `/v1/tokens/{mint}/insiders/graph` | Bundle/linked-wallet detection | 2 RPS (shared bucket) |
| **DexScreener** | Price, liquidity, pair age, volume, sell-side verification | 3 RPS |
| **Jupiter** (v6 API + price/quote) | Buy/sell quotes, slippage check, liquidity depth | 5 RPS |
| **Nansen / GMGN / Apify** | Wallet discovery (via existing curation scripts) | Existing limits |

### Explicitly rejected

- **Birdeye paid** — redundant with Helius DAS + RugCheck
- **Bitquery** — RugCheck's free insiders graph covers bundle detection
- **Twitter/social scraping** — v1 narrative score hardcoded to 50; add later if needed

## 4. Architecture

### Runtime topology

```
┌─────────────────────── runner-intel container ─────────────────────────┐
│  main.py ── asyncio.gather ── all services                             │
│                                                                        │
│  ├── Ingest Layer                                                      │
│  │     ├── WalletMonitor (Helius WS, ported from smc)                  │
│  │     ├── TransactionParser → BuyEvent                                │
│  │     └── event_bus (asyncio.Queue)                                   │
│  │                                                                    │
│  ├── Cluster Engine                                                    │
│  │     ├── WalletRegistry (reads shared wallets.json)                  │
│  │     ├── WalletTier (A/B/C cache, nightly rebuild)                  │
│  │     ├── ConvergenceDetector (A+B only, sliding window)              │
│  │     └── cluster_signal_bus                                          │
│  │                                                                    │
│  ├── Enrichment Layer                                                  │
│  │     ├── TokenMetadata (Helius DAS)                                  │
│  │     ├── PriceLiquidity (DexScreener + Jupiter)                      │
│  │     ├── DeployerHistory (Helius tx history)                         │
│  │     └── enriched_token_bus                                          │
│  │                                                                    │
│  ├── Filter Pipeline                                                   │
│  │     ├── RugGate (RugCheck + hard gates) ← HARD FAIL possible        │
│  │     ├── HolderFilter (DAS token accounts)                           │
│  │     ├── InsiderFilter (RugCheck insiders graph)                     │
│  │     ├── EntryQualityFilter (price extension + liquidity depth)      │
│  │     └── FollowThroughProbe (5-min async probe)                      │
│  │                                                                    │
│  ├── Scoring Engine                                                    │
│  │     ├── FactorScorer (sub-scores per filter)                        │
│  │     ├── RunnerScorer (weighted combine → Runner Score)              │
│  │     ├── VerdictAssigner (tier from score + gates)                   │
│  │     └── Explainer (builds per-candidate reasoning)                  │
│  │                                                                    │
│  ├── Execution Layer                                                   │
│  │     ├── ExecutorInterface (abstract)                                │
│  │     ├── PaperExecutor (implements) ← v1                             │
│  │     └── LiveExecutor (NotImplementedError stub) ← later             │
│  │                                                                    │
│  ├── Position Manager                                                  │
│  │     └── Trailing SL / breakeven / timeout (ported from smc)         │
│  │                                                                    │
│  ├── Alert Fanout                                                      │
│  │     ├── TelegramAlerter (rich format, score breakdown)              │
│  │     └── WebSocketBroadcaster                                        │
│  │                                                                    │
│  └── Dashboard (FastAPI + WebSocket)                                   │
│                                                                        │
│  Shared:                                                               │
│    utils/http.py     — per-host token-bucket rate limiter              │
│    db/database.py    — aiosqlite, WAL mode, own runner.db              │
│    config/settings.py — pydantic-settings (RUNNER_ prefix)             │
│    config/weights.yaml — hot-reloadable scoring weights/gates          │
│    utils/logging.py  — structlog JSON to stdout                        │
└────────────────────────────────────────────────────────────────────────┘
```

### Architectural principles

1. **Message-bus pipeline.** Each stage reads from one `asyncio.Queue`, writes to the next. Swappable, mockable, replayable from SQLite.
2. **Filters are gates AND scorers.** Each filter returns `FilterResult(passed, sub_scores, evidence, hard_fail_reason)`. Gate failure stops the candidate; pass contributes sub-scores and evidence.
3. **Follow-through is async waiting, not point-in-time.** 5-minute probe window after cluster fires — catches dead clusters before they become paper trades.
4. **Paper executor is separate from scoring.** Verdict produced first, executor decides to enter based on tier. Executor can be disabled entirely (intelligence-only mode via env flag).
5. **Rate-limited shared HTTP client.** Every external call goes through `utils/http.py` token-bucket per host. Backoff + retry on 429/5xx. Queues cleanly instead of erroring during bulk backfills.
6. **Own SQLite DB (`runner.db`).** Zero shared state with `smc.db` at DB level. Shared wallet registry is file-level (`config/wallets.json`).
7. **Explainability first.** Every score has its evidence stored. Dashboard and Telegram alerts read from the same `filter_results` / `runner_scores` tables.

## 5. Scoring model

### Hard gates (fail = Ignore, no alert, no trade)

| Gate | Condition | Source |
|---|---|---|
| Mint authority revoked | RugCheck `risks[]` does not include "Mint Authority still enabled" | RugCheck |
| Freeze authority revoked | RugCheck `risks[]` does not include "Freeze Authority still enabled" | RugCheck |
| LP locked or burned | `lpLockedPct >= 85` OR LP burned | RugCheck |
| Deployer holdings | Deployer wallet < 5% of supply | Helius + RugCheck |
| Top-10 holders (ex LP, ex deployer) | < 70% of supply | Helius DAS |
| Honeypot | DexScreener sell-side exists AND Jupiter sell-quote works | DexScreener + Jupiter |
| Token age | 2 min < age < 72 h | On-chain creation time |
| RugCheck danger tier | Not in RugCheck's "danger" tier | RugCheck |

Hard gate failures log `hard_fail_reason` to `candidates` table but do not send alerts (avoid rug-spam).

### Sub-scores (0-100 each)

**1. Wallet Quality Score — weight 20%**

Wallet tier rebuilt nightly from 30-day rolling cluster outcomes in `runner.db`.

| Tier | Criteria | Points |
|---|---|---|
| A | ≥ 60% win rate AND ≥ 5 trades | 100 |
| B | 35-60% win rate OR (< 5 trades AND positive PnL) | 60 |
| C | < 35% win rate OR negative PnL | excluded from cluster count |
| U | Unknown (< 3 trades) | 40 (provisional) |

Sub-score = mean tier points of wallets in the cluster.

**2. Cluster Quality Score — weight 15%**

Cluster parameters (configurable): minimum **3 A+B wallets** within a **30-minute sliding window**.

- Base: 50
- +10 per wallet above the 3-wallet minimum (cap +30, so 6+ wallets max out)
- **Convergence speed** = time from first to last cluster wallet buy (always ≤ window):
  - 10-20 min: +20 (validated sweet spot per Apr 10 analysis)
  - 5-10 min: +10
  - 20-30 min: +10
  - < 5 min: -20 (possible bundle/coordinated)
  - > 30 min: impossible (excluded by window)

**3. Entry Quality Score — weight 15%**

Price extension since first cluster wallet's entry:
| Extension | Points |
|---|---|
| < 5% | 100 |
| 5-15% | 75 |
| 15-30% | 45 |
| 30-60% | 15 |
| > 60% | 0 |

Freshness modifier (token age at signal):
- < 30 min: +15
- 30 min-2 h: +10
- 2-6 h: 0
- 6-24 h: -10
- > 24 h: -20

Liquidity depth check: if 0.25 SOL buy has > 5% Jupiter slippage, sub-score capped at 40.

**4. Holder Quality Score — weight 15%**

Base 0 + factors (cap 100):
| Factor | Contribution |
|---|---|
| Unique holders > 100 | +30 |
| Unique holders 50-100 | +20 |
| Unique holders 20-50 | +10 |
| Top-10 concentration (ex LP/deployer) < 30% | +30 |
| Top-10 30-45% | +20 |
| Top-10 45-60% | +10 |
| Avg holder wallet age > 30 d | +20 |
| Avg holder wallet age 7-30 d | +10 |
| Holder growth during probe (per 10%) | +5 (cap +20) |

**5. Rug/Risk Score — weight 15%**

Start at 100:
- Subtract RugCheck `score_normalised` directly
- -5 per `risks[]` entry with `level == "warn"` (cap -30)
- LP lock duration: > 6 mo +10, 1-6 mo 0, < 1 mo -15
- RugCheck insiders graph:
  - 0-2 insiders: 0
  - 3-5: -15
  - 6-10: -30
  - 10+: -50 (approaches gate)

**6. Follow-through Score — weight 15%**

Measured at end of 5-min async probe:
| Signal | Points |
|---|---|
| +3 A+B wallets joined | 100 |
| +2 | 80 |
| +1 | 60 |
| 0 joined, price within -5% of entry | 40 |
| 0 joined, price up > 10% | 70 |
| Price dumps > 15% | 0 (dead cluster) |

**7. Narrative/Meta Score — weight 5%**

v1: hardcoded 50 (neutral). Interface in place for future social-signal additions.

### Weighted combine

```
Runner Score =
    0.20 × Wallet Quality
  + 0.15 × Cluster Quality
  + 0.15 × Entry Quality
  + 0.15 × Holder Quality
  + 0.15 × Rug/Risk
  + 0.15 × Follow-through
  + 0.05 × Narrative
```

### Verdict tiers

Boundaries are inclusive on the lower bound, exclusive on the upper, except the top tier.

| Score range | Verdict | Action |
|---|---|---|
| `0 ≤ score < 40` | Ignore | Logged only |
| `40 ≤ score < 60` | Watch | Telegram alert, no trade |
| `60 ≤ score < 78` | Strong Candidate | Rich Telegram alert + paper entry 0.25 SOL |
| `78 ≤ score ≤ 100` | Probable Runner | Priority Telegram alert + paper entry 0.375 SOL |

Any hard gate failure → Ignore regardless of score.

### Explainability

Every candidate stores:
- All sub-scores and their computation inputs (`filter_results` table)
- All gate pass/fail + raw data (`filter_results.evidence` JSON)
- Final weighted breakdown (`runner_scores` table)

Telegram alert template:

```
FROM: RUNNER • STRONG CANDIDATE (72)
Token: $WIFHAT (5HpY...abc1)
Cluster: 4 wallets in 14 min (2 A-tier, 2 B-tier)
Why it passed:
  Wallet Quality    80
  Cluster Quality   70
  Entry Quality     75
  Holder Quality    65
  Rug/Risk          88
  Follow-through    70
Gates: all passed
Paper entry: 0.25 SOL @ $0.00042
SL: -25% | Trail: +30% trigger, +5% lock, 20% below HWM
Mirror: <mint address>
```

### Tuning surface (`config/weights.yaml`)

```yaml
cluster:
  min_wallets: 3                # A+B tier count needed to fire
  window_minutes: 30            # sliding window for convergence
  speed_bonus_sweet_spot: [10, 20]  # min-max minutes for max bonus

gates:
  lp_locked_pct_min: 85
  deployer_max_pct: 5
  top10_max_pct: 70
  token_age_min_sec: 120
  token_age_max_hr: 72

weights:
  wallet_quality:   0.20
  cluster_quality:  0.15
  entry_quality:    0.15
  holder_quality:   0.15
  rug_risk:         0.15
  follow_through:   0.15
  narrative:        0.05

verdict_thresholds:
  watch:            40
  strong_candidate: 60
  probable_runner:  78

position_sizing:
  strong_candidate_sol: 0.25
  probable_runner_sol:  0.375

probe:
  follow_through_minutes: 5

wallet_tier:
  a_tier_win_rate: 0.60
  a_tier_min_trades: 5
  b_tier_win_rate: 0.35
  rebuild_hour_utc: 4           # nightly rebuild at 04:00 UTC
  rolling_window_days: 30

http_rate_limits:
  helius_rps: 10
  rugcheck_rps: 2
  dexscreener_rps: 3
  jupiter_rps: 5
```

Hot-reloadable via mtime watcher. Edit, save, next candidate uses new values.

## 6. File layout

```
meme-trading/runner/
├── main.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── pyproject.toml
│
├── config/
│   ├── settings.py
│   ├── weights.yaml
│   └── weights_loader.py
│
├── db/
│   ├── schema.sql
│   ├── database.py
│   └── migrations/
│
├── ingest/
│   ├── wallet_monitor.py
│   ├── transaction_parser.py
│   ├── rpc_pool.py
│   └── events.py
│
├── cluster/
│   ├── wallet_registry.py
│   ├── wallet_tier.py
│   ├── tier_rebuilder.py
│   └── convergence.py
│
├── enrichment/
│   ├── token_metadata.py
│   ├── price_liquidity.py
│   ├── deployer.py
│   └── enricher.py
│
├── filters/
│   ├── base.py
│   ├── rug_gate.py
│   ├── holder_filter.py
│   ├── insider_filter.py
│   ├── entry_quality.py
│   └── follow_through.py
│
├── scoring/
│   ├── factor_scorer.py
│   ├── runner_scorer.py
│   ├── verdict.py
│   └── explain.py
│
├── executor/
│   ├── base.py
│   ├── paper.py
│   ├── live.py
│   └── position_manager.py
│
├── alerts/
│   ├── telegram.py
│   └── fanout.py
│
├── dashboard/
│   ├── app.py
│   ├── routes/
│   ├── ws.py
│   └── static/index.html
│
├── utils/
│   ├── http.py
│   ├── logging.py
│   ├── solana.py
│   └── time.py
│
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    └── fixtures/
```

### Database schema (`db/schema.sql`)

Tables:
- `buy_events` — raw ingest events
- `cluster_signals` — detected clusters with wallet list, window duration
- `candidates` — one row per cluster signal that reached enrichment
- `filter_results` — one row per (candidate, filter) with pass/score/evidence JSON
- `runner_scores` — final weighted scores + verdict per candidate
- `paper_positions` — entries, current state, exits
- `wallet_tiers` — wallet → tier (A/B/C/U) + computed win rate, nightly rebuild
- `wallet_trades` — flattened trade history for tier computation

All tables have `created_at`/`updated_at`. WAL mode.

## 7. Build plan

| Phase | Deliverable | Sessions |
|---|---|---|
| 1. Foundation | scaffold, config, DB schema, rate-limited HTTP, logging, pytest | 0.5 |
| 2. Ingest | port wallet monitor, parser, RPC pool, event bus, integration test | 0.5 |
| 3. Cluster | wallet registry, tier cache, convergence with A+B filter | 1 |
| 4. Enrichment | token metadata, price/liquidity, deployer lookups | 0.5 |
| 5. Filters | rug gate → holder → insider → entry quality → follow-through | 2-3 |
| 6. Scoring | factor scorer, runner scorer, verdict, explainer, hot-reload | 1 |
| 7. Executor + Position Manager | paper executor, live stub, position manager port | 1 |
| 8. Alerts | Telegram rich format, fanout | 0.5 |
| 9. Dashboard | FastAPI, routes, WS, single-page HTML | 1-2 |
| 10. Integration | e2e tests with recorded fixtures, config reload verified | 1 |
| 11. Deploy | Dockerfile, compose, VPS deploy, Traefik subdomain | 0.5 |
| 12. Observation | run for 3-7 days, tune weights | ongoing |

**Estimated build time:** 11-14 sessions @ 1-2 hours, or ~1 week of intensive work.

**Parallelism:**
- Filters in phase 5 are independent after `FilterResult` interface is locked → parallel subagents
- Dashboard frontend + backend parallelizable
- Ingest port + cluster detector parallelizable

**Commit cadence:** one commit per phase or sub-deliverable, pushed to `rakainu/Leverage`.

## 8. Operating model

- **New system runs in `runner-intel` container on VPS** alongside existing `smc-trading`
- **Old system keeps running as comparison baseline** in its current container
- **Paper trades only in v1**; live executor is a stub
- **Rich trades manually from Telegram signals** if he chooses; paper positions track bot's intended fills for slippage comparison
- **Weekly tuning loop**: edit `config/weights.yaml`, hot-reload, observe next ~20 candidates

## 9. Non-goals / deferred

- Live Jupiter swap execution (stub only)
- Twitter / social narrative scoring (hardcoded 50)
- First-mover wallet ranker
- Token-age-weighted cluster thresholds
- ML-based scoring (all heuristic in v1, structured for later statistical fit)
- Cutover of `smc-trading` — stays running until `runner-intel` is trusted

## 10. Open risks

| Risk | Mitigation |
|---|---|
| RugCheck rate limits during backfill | Shared token-bucket HTTP client + queued retries |
| RugCheck API changes / free tier removed | Interface abstraction; filter module swappable |
| Helius WS drops / reconnect loops | Port existing robust reconnect logic from smc scanner |
| Scoring weights wrong on first deploy | Hot-reload `weights.yaml`, no restart needed |
| Old + new systems both acting on same wallets | New system is paper-only; no on-chain conflict |
| Wallet tier bootstrap — no history on day 1 | All wallets start as U-tier (40 pts); tier rebuilder runs nightly; A+B thresholds lower on first week |

---

**End of spec.**
