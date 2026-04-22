# Binance Skills Hub — Integration Path

This document explains how SqueezeWatch uses the locally-cloned `binance-skills-hub` repo.

**Local clone:** `C:/Users/rakai/binance-skills-hub/` (cloned 2026-04-21 from
[github.com/binance/binance-skills-hub](https://github.com/binance/binance-skills-hub),
shallow, public).

## What the skills hub actually is

It is **NOT a Python SDK**. It is a Markdown-based skills marketplace:

- Each "skill" is a `SKILL.md` file with YAML frontmatter that teaches an LLM agent how to
  use a specific Binance product.
- Most skills are wrappers around **`@binance/binance-cli`** — Binance's Node.js CLI that
  handles auth, profile management, and ergonomic command shorthand for hundreds of REST
  endpoints.
- The skill `skills/binance/binance/SKILL.md` is the umbrella for Spot + USDⓈ-M Futures +
  Convert. The reference page `references/futures-usds.md` lists every USDⓈ-M futures
  endpoint with its parameters — the exact inventory we care about for SqueezeWatch.

## Why we don't shell out to `binance-cli` for Phase 1

We considered routing every scanner request through `binance-cli` instead of `requests`.
For SqueezeWatch's read-only Phase 1 it's the wrong tool:

| Concern | Direct `requests` | `binance-cli` subprocess |
|---|---|---|
| Auth requirement | None — all our endpoints are public | None for public, but the CLI is built around profiles |
| Per-call latency | One HTTP round-trip | HTTP round-trip + Node.js subprocess startup |
| Calls per scan | ~3 per symbol × ~300 symbols = ~900 calls | Same call count, plus 900 subprocess spawns |
| Dependencies | `requests` (already in `requirements.txt`) | Node.js 22+, npm install of `@binance/binance-cli` |
| Output handling | `r.json()` | Parse stdout, handle non-JSON CLI noise |
| Rate-limit handling | Our retry/backoff in `binance_client.py` | Still our job (CLI just surfaces the 429) |

The CLI shines when we move to **authenticated** flows (account balance, place order,
manage leverage) — that's Phase 3+ territory, well outside SqueezeWatch's scope.

**Decision:** keep `src/binance_client.py` as our read path. Document that the endpoint
inventory and parameter names were validated against the skills hub's `futures-usds.md`.

## What we DO use the skills hub for

### 1. Authoritative endpoint reference

`src/binance_client.py` currently wraps these endpoints. All are documented in
`binance-skills-hub/skills/binance/binance/references/futures-usds.md` (Market Data section):

| SqueezeWatch wrapper | Skills-hub canonical name | Binance REST path |
|---|---|---|
| `client.exchange_info()` | `exchange-information` | `/fapi/v1/exchangeInfo` |
| `client.klines()` | `kline-candlestick-data` | `/fapi/v1/klines` |
| `client.premium_index()` | `mark-price` | `/fapi/v1/premiumIndex` |
| `client.funding_rate_history()` | `get-funding-rate-history` | `/fapi/v1/fundingRate` |
| `client.open_interest()` | `open-interest` | `/fapi/v1/openInterest` |
| `client.open_interest_hist()` | `open-interest-statistics` | `/futures/data/openInterestHist` |
| `client.ticker_24hr()` | `ticker24hr-price-change-statistics` | `/fapi/v1/ticker/24hr` |

If Binance renames or deprecates any of these, the skills hub will reflect it first.
Re-pull `binance-skills-hub` and diff `futures-usds.md` before changing client code.

### 2. New endpoints worth adding (Phase 1.5 candidates)

The skills hub surfaced four endpoints we **did not have** that directly map to squeeze
detection. None are wired in yet — flagged for a follow-up if Rich green-lights them:

| Endpoint | Why it helps squeeze detection |
|---|---|
| `long-short-ratio` | Ratio of long-to-short positions across **all** traders. <1.0 confirms shorts outnumber longs — direct evidence of crowded-short bias backing up the funding signal. |
| `top-trader-long-short-ratio-positions` | Same ratio for **top traders only**. When retail is short (low overall ratio) and top traders are long (high top-trader ratio), that divergence is the cleanest "smart money setup" signal in the perp universe. |
| `taker-buy-sell-volume` | Aggregated taker buy vs. sell volume per period. Sustained taker-sell pressure + rising OI = shorts aggressing into the market (loading). Sustained taker-buy + flat price = absorption. |
| `mark-price-kline-candlestick-data` | Premium index OHLC. Useful if we ever want to compare mark vs. last to detect contract-vs-spot dislocations. Lower priority. |

If Rich approves: extend `src/binance_client.py` with these four methods, then add a
**Positioning** component (or sub-component of Funding) to the score. Estimate: ~150 LoC
plus tests, plus a doc update to `scoring-rules.md`. **Will not start without explicit
sign-off.**

### 3. Phase 2 / Phase 3 hooks

The skills hub also contains skills that may slot into later SqueezeWatch phases:

| Skill | Path | Possible role |
|---|---|---|
| `meme-rush` | `skills/binance-web3/meme-rush/SKILL.md` | Real-time meme launch + migration data from Pump.fun / Four.meme. Could feed Phase 2's bias multiplier — replacing CoinGecko's "meme" tag with first-hand launchpad data. |
| `crypto-market-rank` | `skills/binance-web3/crypto-market-rank/SKILL.md` | Rank tables across the crypto market. Could supply the "small-cap / non-major" tilt without us hand-curating a `bias.majors` list. |
| `binance` (auth) | `skills/binance/binance/SKILL.md` | Phase 3 — when we eventually add execution, the binance-cli wrapper handles auth, signing, profiles. |

None are wired in. Listed here so future work knows they exist.

## Operational notes

- **Re-syncing the hub:** `git -C C:/Users/rakai/binance-skills-hub pull --depth 1` to
  refresh. Diff `skills/binance/binance/references/futures-usds.md` against last-known
  state to catch endpoint renames or deprecations.
- **Where the hub lives:** outside the SqueezeWatch tree (and outside Leverage/) on
  purpose — it's a general crypto reference Rich may use for other projects too.
- **Auth:** SqueezeWatch Phase 1 requires no Binance credentials. If we ever wire a path
  that needs auth, follow the skills hub's `references/auth.md` (env vars
  `BINANCE_API_KEY` + `BINANCE_SECRET_KEY`, optional `BINANCE_API_ENV=prod|testnet|demo`).
- **Hub license:** MIT.

## Pinned skills hub commit

Since we shallow-cloned, the working tree reflects HEAD as of the clone date (2026-04-21).
If Binance ships breaking changes upstream, our `binance_client.py` is still the source of
truth — the hub is reference material, not a runtime dependency.
