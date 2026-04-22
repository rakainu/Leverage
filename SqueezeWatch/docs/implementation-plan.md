# Implementation Plan

Phase-by-phase build. Each phase is independently valuable — we can stop at any phase boundary and still have a useful artifact.

## Phase 1 — Binance-only MVP (target: single-session build)

Everything runs locally, reads from Binance public REST, writes JSON + CSV to disk.

### 1.1 Scaffold (this commit)
- [x] Folder tree, README, docs, configs, placeholders.

### 1.2 Binance client
- [ ] `src/binance_client.py` — thin wrapper around `requests` with retry + rate-limit handling.
- [ ] Rate limit: Binance futures public is ~2400 weight/min. Scanner hits ~500 symbols × ~5 endpoints; batch where possible.
- [ ] Fixtures captured once and committed to `tests/fixtures/binance/`.
- [ ] Tests: parse each endpoint's shape; exercise retry on 429.

### 1.3 Universe fetch
- [ ] `src/scanner.py::fetch_universe()` — filter exchangeInfo → USDT-M PERPETUAL TRADING symbols.
- [ ] Ban-list in config for obviously-dead or stablecoin pairs (USDC, BUSD, etc.).

### 1.4 Feature extraction
- [ ] For each symbol, pull the raw features needed by scoring (see `docs/scoring-rules.md`):
  - 30 days of 1d klines
  - current funding + last 14d funding
  - OI now + OI 7d ago + OI 14d ago (via openInterestHist 1d)
  - 24h quote volume
- [ ] Output: a `FeatureBundle` dict per symbol, saved to `data/snapshots/YYYY-MM-DD-raw.json`.

### 1.5 Scoring
- [ ] `src/scoring.py::squeeze_score(features, weights)` — pure function, deterministic.
- [ ] Unit tests for each component against hand-calculated cases.
- [ ] Weights configurable via `config/config.json` → `scoring.weights`.

### 1.6 History + persistence
- [ ] Append to `data/history/scores.csv`: one row per symbol per day.
- [ ] Write `data/snapshots/YYYY-MM-DD.json` with full scored detail.

### 1.7 Day-over-day compare
- [ ] `src/compare.py` — load today + yesterday (or last available), produce the diff buckets.
- [ ] Handle the "first run, no yesterday" case cleanly.

### 1.8 Alert formatting
- [ ] `src/alerts.py::format_daily_digest(scored, diff)` → string.
- [ ] Templates in `alerts/templates/telegram.md` + `discord.md`.
- [ ] Write to `outputs/daily/YYYY-MM-DD.md`.

### 1.9 CLI entry point
- [ ] `src/main.py` — single command: `python -m src.main scan` runs steps 1.3 → 1.8 end-to-end.
- [ ] Flags: `--dry-run` (don't write), `--top N`, `--config path`.

## Phase 2 — Companion data sources

Only start after Phase 1 runs clean for ≥7 days and produces a useful daily digest.

- [ ] `src/sources/coingecko.py` — market cap, age, category (meme/new).
- [ ] `src/sources/coinalyze.py` — cross-exchange OI, liquidation feed.
- [ ] Extend score with meme/small-cap multiplier + liquidation-pressure component.

## Phase 3 — Delivery

- [ ] Deploy scanner as a daily cron on the VPS (`46.202.146.30`).
- [ ] Telegram bot posts the digest to Rich's DM.
- [ ] Alert thresholds in `docs/alert-rules.md` (score > X, new entry, big riser).

## Phase 4 — Feedback loop

- [ ] Track which flagged coins actually squeezed in the following 7/14/30 days.
- [ ] Monthly report: hit rate, median drawdown from flag, median max-favorable move.
- [ ] Use hit-rate data to re-weight scoring components.

## Out of scope

- Auto-execution / trading.
- Pine Script strategies.
- Non-Binance spot or other exchanges' perps (could become Phase 5).
