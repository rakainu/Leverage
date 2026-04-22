# Squeeze Theory — working notes

Why this scanner works (or should work), the microstructure we're betting on, open questions.

## Core thesis

A crowded short + flat price + rising OI is a loaded spring. Price hasn't moved yet because supply and demand are balanced *at current level*, but the positioning is one-sided. Any catalyst — or even just margin stress from a small up-move — triggers forced covering.

The meme / small-cap bias is deliberate: majors rarely squeeze like this because their order books are deep enough to absorb the cover. Small caps with concentrated short positioning can +30% in hours.

## Why flat price matters

Flat + rising OI = disagreement at a price level. Bulls and bears are both building positions. Whoever's right gets paid; whoever's wrong gets liquidated. If funding is negative, the market is *paying* shorts — which means shorts are the marginal overcrowded side.

A rally off a coil is cleaner than a rally off a dip because dip-buyers are early bulls and shorts are still in green; a coil-squeeze means shorts are already breakeven or underwater before the move even starts.

## Why negative funding matters

Funding is the perp market's rent. Negative funding = shorts pay longs. That only happens when shorts meaningfully outnumber longs (exchange mechanically balances via funding). Negative funding over 14 days is a persistent short-crowded state.

Funding *flipping* from positive to negative is the richer signal — it means positioning is rotating. Hence the 10-point bonus in the formula.

## Why OI growth matters

Price flat + OI up = new money is entering the market at this level, not unwinding. Price flat + OI down = old positions are closing, nothing replacing them. The former is a coil; the latter is the end of interest.

## Why we penalize pumps

A coin that's already +50% on the 30d chart has burned the short crowd. The positioning is neutralized. The squeeze already happened (or never will — shorts capitulated).

## Open questions

- **How long can a coil last before it's just dead?** Anecdotally, 2–6 weeks of tight range with rising OI. >8 weeks and OI often starts fading — the thesis dies of boredom.
- **Do we need a second-derivative signal?** Score *acceleration* (today's delta > yesterday's delta) might matter more than absolute score.
- **Sector rotation:** if every AI coin squeezes in the same week, is that one signal or N signals? May need a correlation penalty later.
- **Listing age:** too-fresh listings have unreliable funding (liquidity games) and unreliable OI history. Current rule: min 7 days. Might need 14.

## What would break this

- **Exchange-wide short squeeze like Oct 2023:** every coin goes up, our score looks prescient but it was market beta.
- **Funding rate reset / exchange changes:** Binance has occasionally adjusted funding caps, which changes what "strongly negative" means. Re-calibrate thresholds if this happens.
- **Stablecoin depeg:** quote-volume filters get noisy. Would need a cross-check.

## Research references

- (to fill in as we go — papers, threads, case studies on real historical squeezes)
