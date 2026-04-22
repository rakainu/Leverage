# Data Schema

Two persistence layers:

1. **`data/snapshots/YYYY-MM-DD.json`** — full detail, one file per scan, source of truth.
2. **`data/history/scores.csv`** — append-only flat file, fast diff/plot queries.

## Snapshot JSON

One file per scan run, named by UTC date.

```json
{
  "run_timestamp_utc": "2026-04-21T14:00:00Z",
  "run_id": "2026-04-21-001",
  "universe_size": 487,
  "config_hash": "sha256:...",
  "binance_endpoints_hit": 4,
  "errors": [
    {"symbol": "FOOUSDT", "reason": "OI history <14d", "endpoint": "openInterestHist"}
  ],
  "symbols": [
    {
      "symbol": "DOGEUSDT",
      "base_asset": "DOGE",
      "onboard_date": "2020-07-10",
      "age_days": 2112,

      "price_last": 0.145,
      "return_7d": 0.021,
      "return_14d": -0.008,
      "return_30d": -0.035,

      "range_pct_14d": 0.042,
      "rv_21d": 0.56,

      "funding_rate_now": -0.00012,
      "funding_rate_avg_14d": -0.00008,

      "oi_now": 54123456.0,
      "oi_7d_ago": 41200000.0,
      "oi_14d_ago": 38100000.0,
      "oi_growth_7d": 0.3137,
      "oi_growth_14d": 0.4206,

      "quote_volume_24h": 148000000.0,

      "component_scores": {
        "flatness": 82,
        "funding": 90,
        "oi_growth": 100,
        "non_pumped": 100,
        "liquidity": 100
      },
      "composite_raw": 93.4,
      "bias_multiplier": 1.00,
      "squeeze_score": 93.4,
      "rank": 1
    }
  ]
}
```

### Field notes

- `config_hash`: SHA-256 of `config.json` at run time. Makes ranking reproducibility auditable.
- `errors`: non-fatal — scanner continues. Fatal errors abort the run with no snapshot written.
- `component_scores[k] = null`: data was missing. Composite re-normalized weights over non-null components.
- `squeeze_score`: after bias multiplier. `composite_raw`: before.
- `rank`: 1-indexed. Ties broken per `docs/scoring-rules.md`.

## History CSV

Append-only, one row per (symbol, date). Fast to load into pandas for diffs and plots.

**Path:** `data/history/scores.csv`

**Columns:**
```
date, symbol, rank,
squeeze_score, squeeze_score_100,
composite_raw, bias_multiplier,
flatness_score, funding_score, oi_growth_score, non_pumped_score, liquidity_score,
price_last, return_7d, return_30d,
funding_now, funding_avg_14d,
oi_growth_7d, oi_growth_14d,
quote_volume_24h, config_hash
```

`squeeze_score` is the user-facing 0–10 (one decimal). `squeeze_score_100` is the
internal 0–100. Both are kept so old rows stay re-rankable.

**Types:**
- `date`: ISO date (YYYY-MM-DD), UTC run date.
- All `*_score` columns: int 0–100, or empty string for null.
- `price_last`, `return_*`, `funding_*`, `oi_growth_*`, `quote_volume_24h`: float.
- `config_hash`: string.

**Write rules:**
- Header is written only if the file doesn't exist.
- One run = one batch of rows. If a row with the same (date, symbol, config_hash) already exists, the run errors out — history is write-once per day-config.

## Raw feature snapshots (optional, Phase 1.4)

Before scoring, the scanner can dump the raw FeatureBundles to `data/snapshots/YYYY-MM-DD-raw.json`. Useful for re-scoring historical data with a new formula without re-hitting Binance.

Enabled via config: `scanner.save_raw_features: true`.

## Output digests

`outputs/daily/YYYY-MM-DD.md` — rendered digest (Markdown, shared between Telegram and Discord with template-specific formatting adjustments). Not gitignored ambiguity: these ARE gitignored per `.gitignore`, but the directory is kept via `.gitkeep`.

## Retention

- Snapshots: keep forever (they're small — ~200 KB each). 5 years ≈ 400 MB.
- History CSV: keep forever. A year of daily scans of 500 symbols ≈ 180k rows ≈ 30 MB.
- Raw feature snapshots: retain 90 days rolling if enabled (pruned by a maintenance script — TODO Phase 2).

## Migration policy

Schema changes: bump `schema_version` in snapshot JSON (add the field when first change lands). Never silently reuse an old field for a new meaning.
