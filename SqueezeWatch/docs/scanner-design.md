# Scanner Design

How the pieces fit. Each module has one job and one set of dependencies.

## Data flow

```
                   ┌─────────────────────────┐
                   │  Binance public futures │
                   │      REST endpoints     │
                   └───────────┬─────────────┘
                               │
                    src/binance_client.py
                               │
                               ▼
           ┌────────────── fetch_universe() ──────────────┐
           │             src/scanner.py                   │
           │  • filters TRADING/PERPETUAL/USDT            │
           │  • applies ban-list                          │
           └──────────────────────┬───────────────────────┘
                                  │
                                  ▼
           ┌─────────── extract_features(symbol) ─────────┐
           │             src/scanner.py                   │
           │  • klines 30d, funding 14d, OI 14d, vol 24h  │
           └──────────────────────┬───────────────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────┐
                    │      src/scoring.py     │
                    │  squeeze_score(features)│
                    │   • flatness 0-100      │
                    │   • funding 0-100       │
                    │   • oi_growth 0-100     │
                    │   • non_pumped 0-100    │
                    │   • liquidity gate      │
                    │   → composite 0-100     │
                    └───────────┬─────────────┘
                                │
                                ▼
             ┌──────────────── src/history.py ────────────┐
             │  write_snapshot()  → data/snapshots/       │
             │  append_history()  → data/history/scores.csv│
             └──────────────────┬─────────────────────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   src/compare.py      │
                    │   diff(today,yday)    │
                    └───────────┬───────────┘
                                │
                                ▼
                    ┌───────────────────────┐
                    │   src/alerts.py       │
                    │   format_digest()     │
                    └───────────┬───────────┘
                                │
                                ▼
                        outputs/daily/*.md
```

## Modules

### `src/binance_client.py`
**Does:** HTTP-level wrapper around Binance futures public REST. Handles retry, rate-limit backoff, JSON parsing.
**How to use:** `client.klines(symbol, interval, limit)`, `client.funding(symbol)`, `client.open_interest_hist(symbol, period)`.
**Depends on:** `requests`. Nothing else in this project.
**Tests:** Against committed JSON fixtures. One live-smoke test in `tests/test_live_binance.py` that's marked `@pytest.mark.live` and skipped in CI.

### `src/scanner.py`
**Does:** Universe selection and per-symbol feature extraction. This is the orchestrator.
**How to use:** `scanner.fetch_universe(config)` → list of symbols; `scanner.extract_features(client, symbol)` → `FeatureBundle`.
**Depends on:** `binance_client`. No scoring logic here.

### `src/scoring.py`
**Does:** Pure math. Takes a `FeatureBundle` + `weights`, returns a `Score` with component breakdown.
**How to use:** `scoring.squeeze_score(features, weights)` → `Score`.
**Depends on:** `numpy`. No I/O, no HTTP. Fully unit-testable.

### `src/history.py`
**Does:** Persists snapshots and appends the daily history row. Reads old snapshots by date.
**How to use:** `history.write_snapshot(scored, date)`, `history.load_snapshot(date)`, `history.append_history(scored, date)`.
**Depends on:** `pandas`, stdlib `json`.

### `src/compare.py`
**Does:** Given two snapshots, produce the diff buckets: new_entries, score_risers, score_fallers, exits.
**How to use:** `compare.diff(today_scored, yesterday_scored, top_n)` → `DiffReport`.
**Depends on:** `history` (for loading). Pure logic otherwise.

### `src/alerts.py`
**Does:** Templating. Takes `scored` + `DiffReport` + `template_name` → formatted string.
**How to use:** `alerts.format_daily_digest(scored, diff, template="telegram")`.
**Depends on:** stdlib only. Templates read from `alerts/templates/`.

### `src/main.py`
**Does:** CLI entry. Wires the above modules together into `scan` and (future) `report` subcommands.

## Failure modes

| Scenario | Behavior |
|---|---|
| Binance 429 rate limit | Exponential backoff in `binance_client`, up to 3 retries. |
| Symbol missing OI history (new listing) | Skip OI component, mark `oi_growth_score = None`, composite uses remaining components re-normalized. |
| First run (no yesterday) | Compare produces empty diff buckets, digest skips the "changes" section. |
| Partial failure mid-scan | Scanner collects what it can, logs failed symbols, writes partial snapshot with a `errors` field listing them. |

## Determinism

Given the same snapshot files and same config, ranking MUST be identical across runs. No randomness, no time-dependent defaults. All tiebreakers explicit (see `scoring-rules.md`).
