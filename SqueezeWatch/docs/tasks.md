# Starter Task List

Tracked checklist for Phase 1. Work top-to-bottom — don't skip ahead. Each block maps to a section in `implementation-plan.md`.

## Phase 1.2 — Binance client

- [ ] Create `src/binance_client.py` with:
  - [ ] `BinanceClient(base_url, timeout, max_retries)` class
  - [ ] `.klines(symbol, interval, limit)` → list of bars
  - [ ] `.funding_rate_history(symbol, limit)` → list of rates with timestamps
  - [ ] `.premium_index(symbol=None)` → current funding snapshot
  - [ ] `.open_interest(symbol)` → current OI
  - [ ] `.open_interest_hist(symbol, period, limit)` → OI history
  - [ ] `.ticker_24hr(symbol=None)` → 24h stats
  - [ ] `.exchange_info()` → symbols + metadata
  - [ ] Retry with exponential backoff on 429, 5xx (max 3 retries)
- [ ] Capture fixtures for each endpoint → `tests/fixtures/binance/<endpoint>.json`
- [ ] `tests/test_binance_client.py`: parse fixture, exercise retry on mocked 429
- [ ] `tests/test_live_binance.py` marked `@pytest.mark.live` for smoke testing real API

## Phase 1.3 — Universe

- [ ] `src/scanner.py::fetch_universe(client, config)`:
  - [ ] Filter exchangeInfo → status=TRADING, contractType=PERPETUAL, quoteAsset=USDT
  - [ ] Exclude ban-list from config (stablecoin pairs, known-dead tickers)
  - [ ] Return list of `UniverseSymbol(symbol, base_asset, onboard_date, age_days)`
- [ ] Test with captured exchangeInfo fixture

## Phase 1.4 — Feature extraction

- [ ] `src/scanner.py::extract_features(client, universe_symbol)`:
  - [ ] Pull 30d of 1d klines
  - [ ] Pull 14d of funding rates (42 entries approximately)
  - [ ] Pull OI history 1d period, last 14+ days
  - [ ] Pull 24h ticker
  - [ ] Return a `FeatureBundle` dict
- [ ] `src/scanner.py::extract_all(client, universe, config)`:
  - [ ] Run extraction across the universe
  - [ ] Collect `errors` list for symbols that failed
  - [ ] Respect rate limit: ≤ 2400 weight/min
- [ ] Save raw bundles if `scanner.save_raw_features: true` in config

## Phase 1.5 — Scoring

- [ ] `src/scoring.py` with pure functions:
  - [ ] `flatness_score(features, universe_percentiles)`
  - [ ] `funding_score(features)`
  - [ ] `oi_growth_score(features)` — returns None if missing history
  - [ ] `non_pumped_score(features)`
  - [ ] `liquidity_score(features)`
  - [ ] `composite(component_scores, weights)` — handles None components
  - [ ] `squeeze_score(features, weights, bias_fn)` — top-level
- [ ] Unit tests for each component against hand-calculated inputs
- [ ] Unit tests for weight re-normalization when components are None
- [ ] Unit tests for tiebreakers

## Phase 1.6 — Persistence

- [ ] `src/history.py::write_snapshot(scored, run_id, errors, config_hash)`
- [ ] `src/history.py::load_snapshot(date)` → returns None if missing
- [ ] `src/history.py::append_history(scored, date, config_hash)`
- [ ] Tests: write→load round-trip; duplicate-row guard

## Phase 1.7 — Compare

- [ ] `src/compare.py::diff(today, yesterday, top_n=30)`:
  - [ ] `new_entries`: in today's top_n but not yesterday's, with score ≥ 60
  - [ ] `score_risers`: delta ≥ 15 AND today score ≥ 50
  - [ ] `score_fallers`: delta ≤ -15 AND yesterday score ≥ 50
  - [ ] `graduations`: yesterday rank ≤ 20 AND today return_7d > 0.15
- [ ] Handle None yesterday (first run) — return empty buckets
- [ ] Tests for each bucket's edge conditions

## Phase 1.8 — Alerts

- [ ] `alerts/templates/telegram.md` — final template with {placeholders}
- [ ] `alerts/templates/discord.md` — same, formatted for Discord
- [ ] `src/alerts.py::format_daily_digest(scored, diff, template_name)` → string
- [ ] Length-aware split for telegram (4096) and discord (2000)
- [ ] Write `outputs/daily/YYYY-MM-DD.md`

## Phase 1.9 — CLI

- [ ] `src/main.py` with argparse or click:
  - [ ] `scan` subcommand runs end-to-end
  - [ ] `--dry-run`: skip writes
  - [ ] `--top N`: override digest top count
  - [ ] `--config PATH`: override config path
- [ ] `python -m src.main scan` works from project root

## Cross-cutting

- [ ] `config/config.example.json` — committed template
- [ ] `config/config.json` — local copy (gitignored)
- [ ] `notes/scoring-changelog.md` — log every weight/threshold change
- [ ] README updated with Phase 1 "how to run" section once CLI is wired
- [ ] First real-data run + commit of first snapshot + sanity check on the top-10
