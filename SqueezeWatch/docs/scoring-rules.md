# Scoring Rules

The Squeeze Score is a 0–100 composite of five components. This doc is the source of truth for how each one is computed.

**Status:** v0 proposed formula. Weights and thresholds are starting points — expect to tune after 2–4 weeks of real-data observation.

## Inputs (the FeatureBundle)

Per symbol, the scanner collects:

- `closes_30d`: list of 30 daily closing prices (oldest first)
- `highs_30d`, `lows_30d`: same, for high/low
- `funding_rates_14d`: list of ~3×14 = 42 funding rates (8h cadence on Binance perps)
- `oi_now`, `oi_7d_ago`, `oi_14d_ago`: open interest in contracts
- `quote_volume_24h`: last 24h volume in USDT
- `onboard_date`: symbol's listing timestamp
- `age_days`: convenience — days since onboard

## Components

### 1. Flatness (weight: 0.30)

How tightly has price been coiling?

```
range_pct    = (max(highs_14d) - min(lows_14d)) / mean(closes_14d)
rv_21d       = stdev(daily_log_returns_21d) * sqrt(365)
flatness_raw = -1 * (0.5 * range_pct + 0.5 * rv_21d)
flatness_score = clamp(map(flatness_raw, p10..p90 across universe), 0, 100)
```

Universe percentile mapping: rank symbol's `flatness_raw` against all symbols this run. p90 (tightest) → 100, p10 (widest) → 0. This is **per-run normalization**, not an absolute scale.

Rationale: tight range + low realized vol = classic coil.

### 2. Funding (weight: 0.20)

Reward negative or zero funding. Penalize high positive.

```
funding_avg_14d = mean(funding_rates_14d)
funding_now     = funding_rates_14d[-1]

# Map basis points to score, piecewise
funding_score =
  100 if funding_avg_14d <= -0.0005    # strongly negative (shorts paying)
  80  if -0.0005 < funding_avg_14d <= 0
  60  if 0 < funding_avg_14d <= 0.0001
  40  if 0.0001 < funding_avg_14d <= 0.0003
  20  if 0.0003 < funding_avg_14d <= 0.0005
  0   if funding_avg_14d > 0.0005

# +10 bonus if funding flipped negative in last 48h
if any(funding_rates_14d[-6:]) < 0: funding_score = min(100, funding_score + 10)
```

Rationale: negative funding = shorts paying longs to hold = crowded short = squeeze fuel.

### 3. OI Growth (weight: 0.25)

Positioning is building up while price isn't moving.

```
oi_growth_7d  = (oi_now - oi_7d_ago)  / oi_7d_ago
oi_growth_14d = (oi_now - oi_14d_ago) / oi_14d_ago

oi_growth_blend = 0.6 * oi_growth_7d + 0.4 * oi_growth_14d

oi_score =
  100 if oi_growth_blend >= 0.30
  80  if 0.20 <= oi_growth_blend < 0.30
  60  if 0.10 <= oi_growth_blend < 0.20
  40  if 0.05 <= oi_growth_blend < 0.10
  20  if 0    <= oi_growth_blend < 0.05
  0   if oi_growth_blend < 0
```

Missing data (new listing, OI history shorter than 14d): component returns `None`, composite re-normalizes weights over remaining components.

Rationale: OI up while price flat = new positioning, not shake-out.

### 4. Non-Pumped (weight: 0.15)

Penalize coins that already made a big move in **either direction**.

```
return_30d = (closes_30d[-1] - closes_30d[0]) / closes_30d[0]
return_7d  = (closes_30d[-1] - closes_30d[-8]) / closes_30d[-8]

max_abs_ret = max(abs(return_7d), abs(return_30d))

non_pumped_score =
  100 if max_abs_ret <= 0.05        # essentially flat
  80  if 0.05 < max_abs_ret <= 0.15
  50  if 0.15 < max_abs_ret <= 0.30
  20  if 0.30 < max_abs_ret <= 0.60
  0   if max_abs_ret > 0.60         # already ran OR already crashed
```

Rationale: we want **pre-move**, not post-move — and not a falling knife. The
theory says "flat / sideways price over 14–30 days." A −40% crash violates that
just as much as a +40% pump; shorts piling in on a crashing asset are not the
same as shorts piling in on coiled price. The symmetric-absolute form was
adopted 2026-04-22 after the first full-universe run surfaced several capitulating
coins (SAGAUSDT −40%, IPUSDT −20%, DYMUSDT −38%) at high ranks — see
`notes/scoring-changelog.md`.

### 5. Liquidity Gate (weight: 0.10, but acts as a floor)

```
liquidity_score =
  0   if quote_volume_24h < 1_000_000   # untradable, disqualify
  50  if 1_000_000 <= quote_volume_24h < 5_000_000
  100 if quote_volume_24h >= 5_000_000
```

**Hard gate:** if `liquidity_score == 0`, total composite = 0 regardless of other components. Untradable is untradable.

Rationale: we need to be able to open a position without eating 3% slippage.

## Composite

```
weights = {flatness: 0.30, funding: 0.20, oi: 0.25, non_pumped: 0.15, liquidity: 0.10}

# If any component is None (missing data), redistribute its weight across remaining.
active = {k: w for k, w in weights.items() if component_score[k] is not None}
total_w = sum(active.values())
weights_norm = {k: w / total_w for k, w in active.items()}

composite = sum(component_score[k] * weights_norm[k] for k in active)
```

Then apply the Phase 1 bias multiplier:

```
bias = 0.9 if symbol in config.bias.majors else 1.0
squeeze_score_100 = clamp(composite * bias, 0, 100)
squeeze_score     = squeeze_score_100 / 10.0   # user-facing 0-10 scale, 1 decimal
```

### Phase 1 bias — "majors demotion" (proxy)

We don't have CoinGecko yet, so we can't tag coins as "meme" / "small-cap" with real data.
Instead Phase 1 uses a hand-curated `bias.majors` list in `config/config.example.json`
(BTC, ETH, BNB, SOL, etc.). Symbols on that list get a 0.9 multiplier — slightly demoted,
not eliminated. Everything else gets 1.0.

This is a documented proxy, not a real meme tag. It tilts the ranking toward
small/mid-caps without inventing data we don't have.

### Bias multipliers (Phase 2 — CoinGecko required)

```
meme_multiplier:
  1.15 if coin is tagged "meme" on CoinGecko
  1.00 otherwise

age_multiplier:
  1.10 if 14 <= age_days <= 120   (new-ish, has some history, not wash-listing fresh)
  1.00 if age_days > 120
  0.95 if age_days < 14           (too fresh, unreliable data)
```

Phase 2 will replace the majors-demotion with these once CoinGecko is wired in.

## Display scale (0–10)

Internal math is on the 0–100 scale (cleaner thresholds for component piecewise scoring).
The user-facing display score is `squeeze_score = squeeze_score_100 / 10.0` (one decimal).

Both are persisted in the snapshot JSON and history CSV so old snapshots can be re-scored
or re-ranked without ambiguity.

Alert thresholds in `docs/alert-rules.md` are stated on the 0–10 scale (e.g., "score crosses 8.0+",
"score jumps by 2.0+ points") to match what Rich sees in the digest.

## Tiebreakers

When two symbols have the same composite:

1. Higher OI growth wins.
2. Lower realized vol wins.
3. Alphabetical (deterministic fallback).

## What we're explicitly NOT scoring on (yet)

- Social metrics (Twitter mentions, Reddit velocity). Too noisy, too gameable.
- Whale wallet accumulation. Requires chain-by-chain work — separate project.
- Liquidation clusters. Phase 2 with Coinalyze.
- News / listing announcements. Out of scope for v1.

## When to revise this doc

- After every backtest cycle (see Phase 4 in implementation-plan.md).
- Any change must be logged in `notes/scoring-changelog.md` with the date and the reason.
- Follow the "one filter change at a time" rule — no stacking weight changes.
