# SqueezeWatch

Standalone scanner and alert system for **Binance USDT-M perpetual futures**. Detects early short-squeeze / accumulation setups in meme coins and lower-cap alts — before they move.

Not a trading bot. Not a Pine indicator. A watchlist builder.

## What it looks for

A coin coiling before it pops:

- **Flat price** over the last 14–30 days (low realized vol, narrow range)
- **Negative or near-zero funding** (shorts paying longs, or flat)
- **Rising open interest** over 7–14 days (fresh positioning)
- **No major pump** already in the last 30 days
- **Tradable liquidity** (enough 24h volume)

Each candidate gets a **Squeeze Score** (0–100). Daily snapshots are saved so score *changes* can drive the "something is waking up" alerts.

## Bias

- Tilt toward meme coins, newer listings, smaller-cap names.
- Don't over-weight majors (BTC, ETH, SOL rarely squeeze like this).
- Don't over-penalize newer listings just for being new.
- Prefer **early and coiling** over **already moving**.

## Data sources

| Source | Role | Status |
|---|---|---|
| Binance USDT-M Futures (public REST) | Primary — price, funding, OI, volume | Phase 1 |
| CoinGecko | Market cap, age, category (meme/new) | Phase 2 companion |
| Coinalyze | Liquidations, aggregated OI cross-exchange | Phase 2 companion |

No API keys required for Phase 1 — Binance public futures endpoints only.

## Status

Phase 0: scaffolding (this commit). See `docs/implementation-plan.md` and `docs/next-steps.md` for build order.

## Quick links

- Build order → [`docs/next-steps.md`](docs/next-steps.md)
- Full plan → [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Scoring rules → [`docs/scoring-rules.md`](docs/scoring-rules.md)
- Scanner design → [`docs/scanner-design.md`](docs/scanner-design.md)
- Alert rules → [`docs/alert-rules.md`](docs/alert-rules.md)
- Data schema → [`docs/data-schema.md`](docs/data-schema.md)
- Skills Hub integration → [`docs/skills-hub-integration.md`](docs/skills-hub-integration.md)
- Task list → [`docs/tasks.md`](docs/tasks.md)
