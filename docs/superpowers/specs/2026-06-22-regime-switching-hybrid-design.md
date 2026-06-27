# Regime-Switching Hybrid — Design

**Date:** 2026-06-22
**Status:** Design (approved, pending spec review)
**Goal:** An actively-trading system that earns **profit every month** by running a
*different* strategy depending on each coin's current market regime. Optimized for
Lighter (zero-fee). BloFin-with-fees performance is reported for awareness, NOT a
pass/fail gate.

## Why this design

This session's testing established, on 3 years of multi-regime data, that:
- Long trend/breakout strategies win in **uptrends**, bleed in chop/down.
- Mean-reversion fades win in **chop/range** (85–91% win rate) but have poor payoff
  and are fee-sensitive — fine on a zero-fee venue, run only in range.
- Nothing profits in **downtrends** unless it can go **short**.

A single strategy can't serve all three. The hybrid assigns each regime its own
specialist, so the book is productive across the whole cycle instead of sitting in
cash — which is the requirement: monthly profit, not a multi-year buy-the-trend wait.

## Success metric (this is what we optimize and report against)

Because the goal is *monthly* profit, the headline is the **monthly P&L distribution**,
not a 3-year total:
- **% of months green** (target: strong majority)
- **average monthly return** and **worst month**
- positive expectancy in each regime individually

Secondary: zero liquidations (hard), max drawdown within the chosen aggressive-but-
recoverable ceiling (default 40% — see Risk), Sharpe, payoff. Reported on Lighter
(primary) and BloFin (informational).

## Architecture (isolated units)

```
regime.py        per-coin regime classifier (Up/Range/Down) on 1h context
strategies/
  long_momo.py   uptrend specialist  (Donchian breakout long, channel-trail, no TP)
  range_mr.py    range specialist    (mean-reversion fade to mean, small targets)
  short_momo.py  downtrend specialist(Donchian breakdown short, channel-trail)
switcher.py      routes coin+bar -> regime -> matching strategy; applies portfolio caps
portfolio_sim.py single shared-capital account: concurrent positions, compounding,
                 honest fills + liquidation model; emits trades + equity curve
report.py        monthly P&L distribution, per-regime attribution, walk-forward
```

Each unit has one job and a clean interface:
- `regime.classify(df_1h, cfg) -> Series[{+1 up, 0 range, -1 down}]` (causal, no lookahead).
- each strategy: `signals(df_exec, cfg) -> [Signal]` (decide on close, fill next bar) —
  reuses the honest engine's Signal contract and fill discipline.
- `switcher.run(coins_data, regimes, cfgs) -> ordered intents` consumed by the portfolio sim.

### 1. Regime classifier
Per coin, on the **1h context timeframe** (execution is faster — see below):
- ADX = trend **strength**; EMA-slope = **direction**.
- **Up**: ADX ≥ `adx_trend` and EMA rising. **Down**: ADX ≥ `adx_trend` and EMA falling.
- **Range**: ADX ≤ `adx_range`.
- Dead-band between `adx_range` and `adx_trend` + **hysteresis**: the regime only flips
  after the new state is confirmed (≥ `confirm_bars`), so it can't flip-flop bar-to-bar.
- Regime read at the 1h close applies to the next execution bars (no lookahead).

### 2. Role-specialized strategies (each tuned only on its own regime's data, then frozen)
| Regime | Strategy | Source | Exec TF | Mechanics |
|---|---|---|---|---|
| Up | long momentum | @millerrh Donchian (real Pine, **already vetted**) | **15m** | Donchian N-bar-high breakout long; exit = trail lower Donchian channel; no fixed TP (let winners run). |
| Range | Connors **RSI-2** mean-reversion (Larry Connors, documented/published) | port from published rules | **5m** | Both-sided fade of short-term extremes: enter when RSI(2) hits an extreme (e.g. <5 long / >95 short) after a band stretch; exit on reversion to a short MA / the mean. |
| Down | Donchian breakdown short (mirror of Up's real logic) | derive from real Donchian | **15m** | Donchian N-bar-low breakdown short; exit = trail upper channel. Earns in downtrends instead of standing aside. |

**Sourcing rule (non-negotiable):** every specialist is a real strategy — a vetted Pine
(Donchian) or a documented published system (Connors RSI-2) — NOT an invented family.
Each is optimized independently on the **bars belonging to its regime only** (via the
classifier) and walk-forward-vetted on its regime before its params are frozen. Vetting
status at design time: Up = done; Down (short mirror) and Range (RSI-2 on crypto) = to be
established in the plan. RSI-2's edge on 5m crypto perps is unproven and must be earned in
walk-forward, not assumed.

### 3. Switcher
For each coin and execution bar: the classifier's current regime selects the single
eligible strategy; only that strategy may OPEN a position on that coin. On a regime
flip while in a position, the active strategy's own exit manages the close (no abrupt
liquidation of open trades); no new counter-regime entries. Portfolio-level caps:
`max_concurrent_positions`, `max_total_notional`, `max_daily_loss` kill-switch.

### 4. Portfolio simulator
A single shared-capital account (not per-coin pooled): positions across coins compete
for the same equity and notional budget, sizing is fixed-fractional risk with
compounding, leverage chosen so liquidation sits clear of the stop (target zero liq).
Honest fills (no lookahead, adverse slippage, funding) consistent with the engine.
This produces the real account equity curve the monthly metrics are computed from.

## Risk / sizing
- **Starting capital: $3,000. Compounding: ON.**
- **Aggressiveness is found empirically, not guessed.** The real dial is risk-per-trade
  (% of equity risked to the stop); the engine then sizes each position's leverage so the
  modeled liquidation price sits ≥ `liq_buffer` × stop-distance away.
- **Leverage/risk sweep (a deliverable):** escalate risk-per-trade (e.g. 1% → 10%) and the
  leverage cap; for each level run the full 3-year hybrid through the portfolio sim and
  record **compounded return, max drawdown, and liquidation count** — across all regimes,
  including the bear stretch (stress-tests the liq model against real adverse moves/gaps).
- **Locked setting = the MOST AGGRESSIVE level that still shows ZERO liquidations AND a
  recoverable max drawdown.** "Still in the game" ceiling defaults to **40% DD** (aggressive
  but climbable); the full frontier is reported so the final dial is set on real numbers.
- Portfolio: max concurrent positions and total notional caps; daily-loss kill-switch
  (these also bound how much aggressiveness can compound into a single bad day).

## Validation plan
1. Per-regime: optimize+walk-forward each specialist on its own regime slices (3y data).
2. Combined: run the full switcher through the portfolio sim, walk-forward across the
   3-year multi-regime span; report the **monthly P&L distribution** + per-regime
   attribution; Lighter primary, BloFin informational.
3. Robustness: Monte Carlo on the combined trade stream; parameter-stability spot check.

## Data
OKX, 3 years (2023-06 → 2026-06), 10 majors at 1h (context + 15m source) and resampled
TFs; 5m for the range specialist (extend history fetch to 5m for the chosen coins).

## Out of scope (YAGNI)
- 4-state / volatility regime (can add later if chop-vs-news separation proves needed).
- Cross-exchange arbitrage, funding carry (separate future module for off-regime).
- ML regime detection (start with interpretable ADX+slope).
- Live deployment wiring (separate step after sim validation).

## Confirmed config
- Starting capital $3,000, compounding on.
- Aggressiveness: maximize compounded return subject to zero liquidations and DD ≤ 40%
  (final dial set from the reported risk frontier).
- Coin universe: the 10 majors already fetched (BTC, ETH, SOL, BNB, DOGE, XRP, ADA, AVAX,
  LINK, LTC); extend 5m history for the range specialist.
