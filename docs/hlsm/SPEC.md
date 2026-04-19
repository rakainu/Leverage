# HLSM — Specification

## What it is

A system that continuously ingests Hyperliquid's full public perp data (every fill, position, funding payment, keyed by wallet), ranks wallets by risk-adjusted profitability, monitors the top-ranked set in real time, and produces three signal classes:

1. **Individual wallet triggers** — "Wallet X just opened a position"
2. **Aggregate positioning shifts** — "Top-100 smart-money net exposure on ETH moved +28% in 6h"
3. **Coordinated flow events** — "4 top-ranked wallets entered BTC short within 45 min"

Output feeds the existing TV→BloFin Leverage bridge as (a) a confluence filter on TV signals, (b) a standalone signal source when conviction is high, or (c) a regime overlay.

## Why it was chosen over alternatives

Selected from a 3-finalist shortlist:

- **Winner: HLSM (this project)** — public verifiable data, no overlap with existing systems, compounding ranking database, starts producing intelligence on day one from 90d of historical API data
- **Runner-up: Strategy Intelligence Layer (SIL)** — wrap the Leverage bridge in journaling + regime-aware filters. Scored marginally higher but requires months of live bridge data before the filters become statistically trustworthy. HLSM starts producing useful output immediately. SIL is the right V4 follow-on project once HLSM is running and produces a clean signal to gate with.
- **Skipped: Perp Positioning Dashboard (liquidations/OI/funding)** — commoditized by Coinalyze, Coinglass, Laevitas. No defensibility.

## Why it fits Rich specifically

- Doesn't overlap with SMC Trading (meme wallets, already running) or Runner Intelligence (memecoin scorer, already running) — same architectural DNA applied to perp wallets, a new asset class with different profit mechanism
- Plugs into existing Leverage bridge as signal input — Rich already has BloFin execution infrastructure
- Matches preference list: wallet tracking, smart-money tracking, ranking/scoring systems, execution systems, alerting
- No massive capital requirement, no institutional data dependency, no impossible latency demands

## Hard constraints this project respects

- Buildable by solo operator with AI help — yes, stack is boring Python + Postgres + Next.js
- Uses public/obtainable APIs — yes, Hyperliquid `api.hyperliquid.xyz` is public and permissionless
- Not dependent on institutional infrastructure — no
- Not dependent on impossible latency advantage — signals have multi-minute decay windows, not microseconds
- Clear MVP path — yes (see PLAN.md)
- Reasonable cost profile — ~$10–30/mo, fits on existing VPS
- Maintainable — yes, aligns with existing Python/Docker patterns

## System architecture

```
DATA SOURCES
├── Hyperliquid REST (info endpoints: userState, userFills, clearinghouseState, userFunding)
├── Hyperliquid WebSocket (real-time positions + fills)
└── Hyperliquid leaderboard scrape (wallet discovery seed set)

INGESTION (Python workers)
├── historical_ingestor.py  — 90d backfill per wallet, paginated
├── live_ws_monitor.py      — subscribed to top-100 wallets
└── leaderboard_crawler.py  — daily refresh of candidate wallets

ENRICHMENT
├── trade_reconstructor     — fills → position timeline → per-trade PnL
├── stats_calculator        — Sharpe, DD, win rate, hold time, R:R
└── style_classifier        — scalper / swing / positional labels

SCORING / RANKING
├── composite_scorer        — weighted 0–100 score per wallet
├── anti_fluke_filter       — min trades, anti-single-trade-dominance
└── rank_publisher          — writes snapshots, detects promotions/demotions

STORAGE (PostgreSQL)
├── wallets
├── fills
├── positions
├── events                  — open/close/resize, timestamped
├── scores_history          — daily snapshot per wallet
├── aggregates              — per-asset net-positioning per 5m bucket
└── signals                 — all generated signals with outcome tagged

SIGNAL ENGINE
├── individual_trigger      — high-score wallet opens position
├── aggregate_shift         — net-positioning delta > threshold
├── coordinated_flow        — N wallets same direction within window
└── signal_publisher        — persists + dispatches

UI / DASHBOARD (Next.js)
├── /ranked                 wallet leaderboard with filters
├── /wallet/:addr           detail, trade history, equity curve
├── /live                   real-time event feed
├── /signals                signal history + backtest results
└── /aggregates             per-asset positioning heatmap

ALERTS / EXECUTION HOOKS
├── telegram_bot            — ranked event alerts
├── webhook_dispatcher      → scalping bridge (V3)
└── daily_report            → Telegram summary
```

## Recommended stack

Boring, reliable, matches existing infra.

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Matches Leverage bridge, SMC, Runner stack |
| Backend framework | FastAPI | Async, clean, fast to ship |
| Frontend framework | Next.js 15 + Tailwind | Matches Runner dashboard |
| Database | PostgreSQL 16 | Proper SQL for aggregations |
| Cache / queue | Redis | WebSocket fan-out + rate-limit tokens |
| Scheduler | APScheduler (in-process) or existing cron | Don't introduce Celery unless V3 forces it |
| Hyperliquid lib | `hyperliquid-python-sdk` | Official, maintained |
| HTTP | `httpx` (async) | Concurrent ingest |
| Stats | `numpy` + `pandas` | Standard, no scikit needed yet |
| Charting (frontend) | `lightweight-charts` (TradingView) | Matches existing TV workflow |
| Alerting | Telegram bot via existing plumbing | Reuse |
| Hosting | Existing VPS 46.202.146.30 | ~$0 marginal cost |
| Deployment | Docker Compose | Matches openclaw pattern |
| Testing | pytest | Matches Runner-Intelligence |

**Deliberately avoided:** Kafka, ClickHouse, Airflow, Terraform, Kubernetes, ML frameworks.

## Operating cost envelope

- MVP: ~$10/mo (fits on existing VPS)
- V2: ~$20/mo
- V3: ~$30/mo

## Signal-gating contract with the Leverage bridge (V3)

HLSM does NOT replace the bridge's execution logic. Bridge remains the sole owner of order placement and exits (per the "bridge owns exits" rule from 2026-04-17). HLSM only provides:

- A queryable "positioning state" endpoint the bridge can check at alert-time
- A verdict enum: `aligned | neutral | conflicting | strong_confluence`
- Optional size-modifier hint (e.g., 0.5x on conflict, 1.5x on strong confluence)

Bridge uses the verdict to gate/size the trade. HLSM never sends execution commands directly. This preserves the strict signal lifecycle Rich established.

## Out of scope (explicitly)

- Building a competing Coinglass/Coinalyze dashboard
- Multi-exchange support before HL-native proves out
- Public SaaS, user auth, payment flow
- ML/AI prediction models — this is a ranking + filtering system, not a forecaster
- Native HL execution (BloFin remains the execution venue)
- Memecoin or spot asset coverage — perps only
