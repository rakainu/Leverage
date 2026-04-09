---
name: TP/SL framework for leveraged crypto
description: Practical rules for take-profit, stop-loss, position sizing, and exit models; synthesized from Rich's training guide + Freqtrade/CME/Kraken/3Commas source material
type: reference
---

## The one rule that matters

**Exits are frameworks, not magic numbers.** Before quoting any TP or SL, identify: (1) market regime, (2) execution environment, (3) risk constraints, (4) stop methodology, (5) TP methodology. Only then discuss numbers — and propose *ranges to test*, not a single answer. If unsure, say "needs testing." Do not guess.

## Risk-per-trade comes before stop distance

Classic **2% Rule** (CME / prop-trading): risk ≤ 2% of total account per trade. Formula:

```
max_loss_usd = account × risk_pct        # e.g. $10,000 × 2% = $200
position_size = max_loss_usd / stop_distance_price
```

Stop distance determines position size, **not** vice versa. Do not "find a stop that lets you take the size you want" — find the size your risk budget allows *for a given valid stop*. In crypto with high leverage, the "risk_pct" should often be lower (0.5–1%) because liquidation cascades happen fast.

**Recovery math** — why capping losses early is non-negotiable:

| Drawdown | Gain needed to break even |
|---|---|
| -5% | +5.3% |
| -10% | +11.1% |
| -20% | +25% |
| -33% | +50% |
| -50% | **+100%** |
| -75% | +300% |
| -90% | +900% |

Losing 50% of an account and then winning 50% leaves you at 75% of the original. Losses compound geometrically. This is why fixed-fractional sizing (always risk the same % of *current* account, not original) is standard.

## Leverage changes the meaning of every stop

At 10x, a 1% price move = 10% of margin.
At 30x, a 1% price move = 30% of margin.

Consequences:
- A "good" SL distance (say 2× ATR) in % terms becomes a huge % of margin at high leverage
- Liquidation buffer shrinks — at 30x, anything beyond ~3% adverse move liquidates
- SL and TP must be co-designed with leverage. Rich trades 10-30x on BloFin, so SL distances ≥1% price are borderline *leverage killers* — the SL protects capital but a single loss eats 10-30% of the trade's margin

Rule of thumb: SL_%_price × leverage ≤ 30-40% of margin per trade, else you lose meaningful equity on a single loss.

## Stop methodologies (valid frameworks)

| Method | Use case | How it works |
|---|---|---|
| **Structure stop** | Mean-reversion at S/R, high-conviction entries | Place SL just beyond last swing high/low — if price breaks structure, thesis is invalidated |
| **ATR stop** | Trend-following, adaptive to volatility | SL at N × ATR from entry (N usually 1.5-3.0). Wider in high vol, tighter in calm. |
| **Percent stop** | Simple systems, backtesting | Fixed % from entry. Easy but ignores current volatility. |
| **Volatility-based** | Statistical models | SL at k × standard deviation of recent returns |
| **Time stop** | Scalps / mean reversion | Exit if not profitable in N bars. Edge decays with time. |
| **Trailing stop** | Let winners run in trending markets | SL moves with price but only in favorable direction. Key params: activation threshold + trail distance. |
| **Break-even promotion** | Protect profit after partial win | Move SL to entry after TP1 hits. What our bridge's P2 step-stop does. |
| **Step-stop (laddered)** | Protect accumulating profit | SL → entry on TP1, SL → TP1 price on TP2, etc. Locks minimum profit at each stage. |

Never mix frameworks randomly. Pick one and tune its parameters.

## TP methodologies (valid frameworks)

| Method | Use case | How it works |
|---|---|---|
| **Fixed R multiple** | Simple, scalable | TP at N × SL distance. N=1 → 1:1 R:R, N=3 → 3:1. Easy to reason about expected value. |
| **Partial TP ladder** | Capture scalps + runners in same trade | Close fraction at each of several TPs. E.g. 40% at 1R, 30% at 2R, 30% at 3R. |
| **Time-based ROI schedule** (Freqtrade's `minimal_roi`) | Strategies where edge decays with time | Dict of minute → required profit. E.g. `{"0": 5%, "15": 3%, "30": 1%, "60": 0%}` — demand more profit early, accept less the longer you hold. |
| **Structure target** | Discretionary + S/R based systems | TP at the next swing high/low, Fibonacci level, or measured move. |
| **Volatility expansion target** | Breakout systems | TP at entry + k × recent range. |
| **Trailing TP** | Capture the full run in trending markets | Activate at initial TP, then trail by a deviation %. Only the LAST TP of a ladder usually trails (3Commas convention). |

## Partial exits > all-in/all-out

Most robust for automated systems. Lets you bank some profit on normal wins while still capturing runners on strong moves. Common splits:

- **40/30/30** — bank fast, taper the runner. Good for high hit-rate scalps.
- **33/33/34** — balanced. Good for unknown hit rate.
- **50/30/20** — heavy early exit. Good when TP1 is most reliable but TP3 rare.
- **20/30/50** — heavy runner. Good for trending systems where TP3 often hits.

**Math that matters (for 40/30/30 with 1×/2×/3× R TPs and break-even SL after TP1):**

- Full runner (all 3 TPs hit): profit = 0.4×1 + 0.3×2 + 0.3×3 = **1.9R**
- TP1 only then breakeven on remainder: profit = 0.4×1 = **0.4R**
- SL hit before any TP: loss = **−1R**

Break-even win rate (at this specific config, assuming full-runner on wins and full-SL on losses):
```
0.4 × 1.9R + 0.6 × (−1R) = 0.76R − 0.6R = +0.16R  (profitable at 40% win rate)
```
So a system that hits 40% of entries to at least TP3 is already profitable with this config. **But** if most wins only reach TP1, break-even shifts dramatically higher:
```
0.5 × 0.4R + 0.5 × (−1R) = 0.2R − 0.5R = −0.3R  (losing at 50% win rate)
```
Requires ~72% win rate if only TP1 reliably fires. Reality check for Pro V3 on 5m: scalp systems often have high TP1 hit rates (>60%) but low TP3 (<20%), so the system can be robust if SL is disciplined but the average win will be small.

## Native-exchange vs bot-simulated exits

| | Native exchange | Bot-simulated |
|---|---|---|
| Latency | Instant (in-engine) | Bot polling cycle + network |
| Reliability during fast moves | High | Low (bot may miss) |
| Works when bot is down | Yes | No |
| Partial fills handled automatically | Usually | Manual reconcile |
| Flexibility (dynamic rules) | Low (static) | High (any logic) |

**Always prefer native** for the basics (SL, static TPs). Use bot logic for anything that needs state (trailing from a specific point, time-based decays, multi-stage promotion). Our bridge does exactly this: entry places real BloFin stop + 3 reduce-only limit orders natively, the poller advances state as fills happen.

## Market regime awareness

Exits must fit the regime, not the trader's preference:

| Regime | Stop style | TP style |
|---|---|---|
| **Strong trend** | Wider trailing / structure | Runner-heavy partials, trail the last tranche |
| **Chop/range** | Tight structure at range edges | Tight fixed targets at opposing range edge |
| **Breakout** | Below breakout base | Measured-move or range-multiple target |
| **High vol** | Wider ATR stops (same $ risk → smaller size) | Wider targets; don't get stopped on noise |
| **Low vol** | Tighter ATR stops (bigger size for same $ risk) | Tighter targets; volatility will mean-revert |

A single TP/SL config applied across all regimes will underperform a regime-aware one. For v1 systems without regime detection, bias toward the "chop" rules because most of the time markets chop and strong trends are rare.

## Key gotchas that kill automated systems

1. **Fees eat scalps.** BloFin maker ~0.02%, taker ~0.06%. Round-trip cost on a scalp = ~0.12%. A "TP1 at 0.6% price move" actually gains ~0.48% after fees. At 10x that's 4.8% of margin, not 6%. Always back fees out of the R:R math.

2. **Slippage in fast moves.** Market entries at volatile times can fill 0.1-0.3% away from quote. Tight stops are vulnerable.

3. **Leverage-SL proximity to liquidation.** At 30x, liquidation is ~3% price. A 2.5% SL means if it misses (slippage), you liquidate. Always leave ≥0.5% cushion between SL and liquidation price.

4. **The stop that "never fills."** At extreme volatility, the SL order may not fill at its trigger price — it fills at whatever price is actually available. Use market stops, not limit stops, for true risk control.

5. **Tight stops + high leverage + noise** = death by a thousand cuts. The system stops out on normal volatility instead of real moves. Default to wider stops than feels comfortable.

6. **Trailing stops that trail too close.** Trail distance should be at least 1 × ATR, often more. Tighter trails get kicked out by normal pullbacks.

7. **TP1 size too large.** If 50% of the position exits at TP1, the remaining 50% needs to hit TP2/TP3 for meaningful PnL. High hit-rate systems can do this; low hit-rate systems should weight later.

## Implications for the BloFin bridge (current state as of 2026-04-09)

Current config: SL = 2.5 × ATR(14), TPs at 1×/2×/3× ATR, split 40/30/30.

**Full-runner R:R** = 1.9 / 2.5 = **0.76:1** (reward less than risk).

**Break-even win rate ≈ 57%** if wins average 1R and losses average 1R. Pro V3 on 5m scalps at calm ATR (0.3-0.6%) gives extremely small absolute PnL numbers — ~$6-$18 per win on $100 margin at 10x, offset by ~$25 losses on SL hits. This is a tight-margin system that needs both (a) a high TP3 hit rate and (b) minimal slippage.

**Changes to consider (all testable via config, no code):**

1. **Tighter SL:** `sl_atr_multiplier` 2.5 → 2.0. Full-runner R:R becomes 1.9/2.0 = **0.95:1**. Lower break-even win rate.
2. **Wider TPs:** `tp_atr_multipliers` [1.0, 2.0, 3.0] → [1.5, 3.0, 5.0]. Full-runner profit becomes 0.4×1.5 + 0.3×3.0 + 0.3×5.0 = 3.0R. R:R = 3.0/2.5 = **1.2:1**. Better, but hit rate of TP3 will drop sharply.
3. **Rebalance split:** 40/30/30 → 25/35/40. Weights the runner. Full-runner = 0.25×1 + 0.35×2 + 0.4×3 = 2.15R. R:R = 2.15/2.5 = **0.86:1**.
4. **Combined:** SL 2.0, TPs [1.5, 3.0, 5.0], split 25/35/40. Full-runner = 0.25×1.5 + 0.35×3 + 0.4×5 = 3.425R. R:R = 3.425/2.0 = **1.71:1**. Break-even win rate ~37%.

None of these are "the answer." All should be A/B tested on demo before committing to one. The current 0.76:1 is the most conservative starting point and happens to match Pro V3's own TP/SL structure, but it's not inherently more correct than the others.

## The training brief (what I will do going forward)

1. Never guess TP/SL numbers.
2. Before every config change, state: current regime, leverage context, R:R math, hit-rate sensitivity, expected value at realistic win rates.
3. Propose ranges to test, not single values.
4. For automation, prefer native exchange stops; use bot logic only for state-dependent behavior (trailing, step-stop, time decay).
5. If uncertain, say "needs testing" and stop.

## Sources

- Rich's training doc: `c:\Users\rakai\Documents\1 Claude\crypto_tp_sl_claude_training_guide.docx`
- Freqtrade Stoploss: https://www.freqtrade.io/en/stable/stoploss/
- Freqtrade Strategy Customization (minimal_roi): https://www.freqtrade.io/en/stable/strategy-customization/
- Freqtrade Leverage: https://www.freqtrade.io/en/stable/leverage/
- 3Commas Take Profit: https://help.3commas.io/en/articles/3108981-how-take-profit-works-smarttrade-and-dca-bots-trailing-feature-explained
- 3Commas Stop Loss: https://help.3commas.io/en/articles/3108977-smarttrade-dca-bots-how-stop-loss-works
- Kraken Bracket/OCO: https://support.kraken.com/articles/take-profit-stop-loss-bracket-orders-derivatives
- BloFin TP/SL help: https://support.blofin.com/hc/en-us/articles/8197974557711
- BloFin API docs: https://docs.blofin.com/index.html
- CME 2% Rule: https://www.cmegroup.com/education/courses/trade-and-risk-management/the-2-percent-rule
- CME Controlling Risk: https://www.cmegroup.com/education/courses/trade-and-risk-management/controlling-risk
