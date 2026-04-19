# HLSM — Profit Mechanics

Three distinct profit mechanisms, ranked by directness of P&L impact.

---

## Mechanism 1 — Individual wallet follow (direct copy-style)

**How it works:**
A ranked wallet (score ≥ 80, 200+ trades, verified 18-month Sharpe ≥ 2, classified style) opens a position. HLSM detects it within ~10 seconds via WebSocket. Your bridge enters the same side on BloFin. You exit when the wallet exits (or on your own risk rules, whichever first).

**Why it makes money:**
You're paying ~20bps in slippage + fees to rent a trading decision made by someone with verified edge. If their historical expectancy per trade is +0.8% and you capture 60% of it after execution drag, you net ~+0.48% per followed trade. Over 200 trades/year that compounds meaningfully.

**Why the edge exists:**
- Hyperliquid's L1 makes every position publicly visible — most retail doesn't mine this
- Those who do typically use the raw public leaderboard (ranks by absolute PnL — biased toward luck + size, not skill)
- Your ranking filters luck, spoofers, wash-traders, and single-trade winners
- Legally public data; copying is permissionless

**Why it persists:**
Skilled traders already move size. Your entry behind their flow doesn't degrade their fill quality, and they can't hide on an L1-transparent venue.

---

## Mechanism 2 — Aggregate positioning as a regime signal

**How it works:**
Track net long/short exposure across top-100 ranked wallets per asset. When aggregate exposure shifts beyond threshold (e.g., +28% in 6h, ≥3σ move), treat as regime signal. Enter the aggregate direction on a chosen horizon.

**Why it makes money:**
When a population of independently-skilled traders swings the same direction in a short window, they're reacting to *something* — macro, orderflow, on-chain, pattern. You don't need to know what. Forward returns historically skew in the aggregate direction.

**Why the edge exists:**
- Coinglass/Coinalyze publish aggregate OI/funding across *everyone* — noise-dominated because retail is the bulk
- Your aggregate is filtered to verified-skilled wallets only — structurally cleaner signal
- No productized competitor offers "smart-money-only aggregate positioning"

**Why it persists:**
Even if skilled wallets knew they were watched, they can't hide (L1 transparency). Their only option is to trade elsewhere — but HL has the deepest on-chain perp liquidity.

---

## Mechanism 3 — Confluence filter on the Leverage bridge (highest-ROI use)

**How it works:**
TV alert fires → bridge receives webhook → bridge queries HLSM state for that asset → HLSM returns verdict `aligned | neutral | conflicting | strong_confluence` → bridge gates/sizes the trade accordingly.

Price-agnostic. Signal-driven. Bridge remains sole owner of execution and exits.

**Why it makes money:**
You don't need HLSM to produce winning trades standalone — only to *correlate with outcomes of trades you're already making*. If confluence improves TV bridge hit rate from 52% to 60% and conflict-filtering removes 25% of losing trades, compounding impact on existing volume is large.

**Why this is the highest-ROI mechanism:**
Leverages trades you already execute. No new capital. No new venues. Pure quality filter.

---

## The compounding layer (why it improves over time)

Unlike arbitrages that die when copied, this has positive feedback:

1. **Ranking database improves with data** — a wallet with 6 months of observed trades produces cleaner scoring than one with 2 weeks. Time passes, scores improve.
2. **Signal outcomes get labeled** — every emitted signal gets tagged with what happened after. After 500 signals you own decay curves, regime dependencies, asset-specific performance data that's impossible to buy.
3. **Filter quality compounds** — anti-fluke rules, style classifier, aggregate thresholds all get tuned against growing real data.
4. **New mechanisms unlock** — coordinated flow detection needs a baseline of "normal" behavior, only available after months of observation.

---

## Realistic profit envelope

| Scenario | Mechanism used | Plausible annual return delta |
|---|---|---|
| Pessimistic | Aggregate positioning as a weak regime filter | +10–25% / yr over baseline |
| Base case | Confluence filter on TV bridge + selective individual follows | +30–80% / yr |
| Optimistic | All three mechanisms tuned, coordinated-flow working | +100–250% / yr |
| Failure mode | Scorer overfits, decay too fast, or wallets turn out lucky | -execution costs (~-5% / yr) |

Asymmetry matters: downside capped at op cost + small execution drag, upside scales with capital and ranking quality.

---

## Standalone value even if execution edge is weak

A ranked, style-classified, risk-adjusted Hyperliquid wallet database with 6+ months of history is independently valuable:

- Subscription data product (niche, small user base, $50–200/mo tier)
- Weekly newsletter input ("what smart money did this week")
- Raw intelligence shareable/tradeable with other operators
- Input to any future trading system built by Rich or others

This is the backstop that justifies building regardless of execution outcome.

---

## One-line version

Pay ~20bps in execution cost to rent the trading decisions of verified-skilled traders whose moves are legally and permanently public, using a system that filters luck from skill better than raw leaderboards — and use their aggregate flow as a quality filter on leveraged trades already being made.
