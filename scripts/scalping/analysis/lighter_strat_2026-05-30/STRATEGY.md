# Volatility Compression → Expansion (Squeeze) — Lighter Strategy Package (2026-05-30)

**One-line:** Trade a **4-coin basket (SOL/ETH/ZEC/HYPE)** on the **1h** chart. When
Bollinger bands compress inside Keltner channels (volatility squeeze) for ≥10 bars,
then release, enter **in the direction of the release** and ride the expansion with a
trailing stop. Long **and** short. Designed for Lighter zero-fee perps; also survives
BloFin fees.

> **Verdict: paper-trade candidate that meets the stated pass criteria at the
> portfolio level** (PF ≥ 1.30, maxDD < 20%, ≥ 80 trades, positive OOS, 3/3
> walk-forward folds positive, generalizes across coins, statistically significant
> in-sample t = 2.45). Read the caveats in §8 — it is a basket/momentum strategy with
> a low win rate, not a high-certainty single-coin edge.

---

## 1. Final strategy rules (plain English)

For **each** of SOL, ETH, ZEC, HYPE on the **1-hour** chart, independently:

1. **Compression:** compute Bollinger Bands(20, 2σ) and Keltner Channels(20, 1.5×ATR14).
   A bar is "squeezed" when the Bollinger band sits **entirely inside** the Keltner channel.
2. **Trigger:** when the squeeze has lasted **≥ 10 consecutive bars** and then **ends**
   (bands pop back outside Keltner) → that bar is a **release**.
3. **Direction:** if release close **> 20-SMA basis → go long**; if **< basis → go short.**
   (Direction is decided by the data, not a fixed bias — see §3.)
4. **Entry:** market order on the next bar open (expansion needs to be taken; limit
   entries miss it).
5. **Initial hard stop:** 1.5 × ATR(14) from entry (always on).
6. **Trailing stop:** trail 3.0 × ATR(14) behind the **best close** since entry (close-based,
   never intrabar wicks). The trail is what captures the expansion — it only tightens as
   price runs in favor.
7. **Time stop:** exit after 48 bars (48h) if neither stop has triggered.
8. **One position per coin at a time. No pyramiding, no averaging, no grid.**

## 2. Exact tested market / timeframe
- Instruments: **SOL, ETH, ZEC, HYPE** perpetuals (BloFin OHLCV, 180d: ~2025-12-01 → 2026-05-30).
- Timeframe: **1h** (resampled from 5m). 5m and 15m were tested and **rejected** (see research log).

## 3. Long / short / both
**Both.** The search did **not** start short-biased. The cross-instrument triage tested
long, short, and both; the squeeze release works in both directions, with the *both-sided*
version being the most robust (it doesn't depend on cherry-picking a direction). Long and
short are taken purely on the sign of the release relative to the mean.

## 4. Backtest summary (4-coin portfolio, $1,000, 1% risk/trade, merged equity)

| Metric | Lighter (0 fee, slip .05%) | BloFin fees | Lighter slip .10% |
|---|---|---|---|
| Profit factor | **1.76** | 1.54 | 1.54 |
| Net return | +114% | +82% | +81% |
| Win rate | 34% | 33% | 32% |
| Avg R / trade | +0.43 | +0.34 | +0.33 |
| Max drawdown | 17.3% | 18.3% | 17.8% |
| Trades | 192 | 192 | 193 |
| Worst losing streak | 11 trades (−13.8%) | 18 (−20%) | 18 (−19.5%) |
| t-stat (pooled R) | **2.45** | 1.97 | 1.94 |

Per-coin (FULL, Lighter): SOL PF 1.11 · ETH 1.32 · ZEC 3.22 · HYPE 1.65 — **all positive**.
Profit concentration: top-3 wins = **15%** of gross (not outlier-driven). Months: **6/7 positive**.

## 5. OOS and walk-forward

| Slice | PF | n | maxDD | notes |
|---|---|---|---|---|
| In-sample (70%) | 1.57 | 141 | 17.3% | tuned here (pooled across coins) |
| **Out-of-sample (30%)** | **1.81** | 50 | 7.7% | OOS > IS (anti-overfit) |
| WF fold 0 (test) | 3.08 | 16 | 6.2% | positive |
| WF fold 1 (test) | 2.93 | 19 | 3.6% | positive |
| WF fold 2 (test) | 2.47 | 15 | 3.9% | positive |

Per-coin OOS: SOL 1.12 · ETH **0.52** · ZEC 5.05 · HYPE 3.27 → **3/4 coins positive OOS**
(ETH is the weak one). Tuning was done on **pooled IS across all 4 coins**, never per-coin,
to avoid curve-fitting any single instrument.

## 6. Slippage & fee assumptions
- **Lighter (primary):** maker 0% / taker 0% fee. Slippage **0.05%** applied to market
  entries and stop fills only (maker/limit fills get no slippage). Funding 0.01%/8h on held notional.
- Stress: 0.10% slippage → PF 1.54 (still passes). **BloFin** (taker .06% / maker .02% / slip .05%)
  → PF 1.54. **The edge is not purely zero-fee-dependent** — the trailing exit holds positions
  long enough that fees are a minor drag. It is *better* on Lighter (PF 1.76 vs 1.54).

## 7. Risk settings for Lighter paper trading

| Setting | Value |
|---|---|
| Instruments | SOL, ETH, ZEC, HYPE perps, 1h |
| Direction | Long + short |
| Risk per trade | **0.75% of equity** (recommended) → backtest maxDD 13.3%. 1% → 17.3%; 0.5% → 9.0% |
| Sizing | notional = risk$ / (1.5×ATR / entry); cap leverage 20×; liq buffer ≥ 2.5× stop |
| Margin mode | Isolated |
| Max positions | 1 per coin (≤ 4 concurrent) |
| Starting paper equity | $1,000 |
| Initial stop | 1.5 × ATR(14) — mandatory on every order |
| Trail | 3.0 × ATR(14) behind best close |
| Time stop | 48h |

## 8. Honest verdict & caveats

**Paper-trade candidate (leaning validated, with conditions).** It is clearly stronger and
more honest than the prior SOL 1h short-MR: that was 1 coin, short-only, t-stat 1.51, and
failed cross-instrument. This one is **4 coins, both directions, t-stat 2.45, OOS > IS, 3/3
walk-forward, and works on BloFin too.** But be clear-eyed:

1. **It's a basket strategy.** Individual coins (esp. SOL, FULL PF 1.11) would not pass alone;
   diversification across 4 coins delivers the robust portfolio metrics. **ZEC is the strongest
   contributor; ETH is negative OOS.** Drop a coin and the numbers move.
2. **Crypto coins are correlated** — "4 coins" is not 4 independent bets. Expansion moves often
   hit the whole market at once, so true diversification is less than the n suggests.
3. **Low win rate (34%), fat-tail profile.** The edge comes from the trailing stop riding the
   occasional large expansion; remove the trail and PF drops to 1.26 (fails). Expect long
   losing streaks (worst was 11 trades). This is a momentum/breakout temperament, not a
   smooth equity curve.
4. **OOS t-stat is only 1.30** (n=50) — the OOS window alone is small; confidence rests on the
   full-sample t=2.45 + 3/3 walk-forward. Forward-testing is needed to confirm.
5. **Not zero-fee-dependent** (good), but **better on Lighter** — so Lighter is the right venue.

## 9. Kill-switch rules for paper testing
- **Hard kill** if portfolio paper **PF < 1.20** after **40 closed trades**, or **maxDD > 18%**.
- **Per-coin review** at 40 trades: drop any coin with PF < 1.0 (watch ETH especially).
- **Streak alarm:** if a live losing streak exceeds **14 trades** (worse than backtest), pause
  and re-audit — the regime may have changed.
- Promote toward live only after **≥ 80 paper trades** with PF ≥ 1.4 and maxDD ≤ 15%.

## 10. Files created / changed
- `scripts/scalping/analysis/lighter_strat_2026-05-30/` — `strat_lib.py` (6 families),
  `common.py` (Lighter cost model + loader), `triage_lighter.py`, `sweep_squeeze.py`,
  `drill_squeeze.py`, `finalize_squeeze.py`, `runs/squeeze_sweep.json`, this `STRATEGY.md`,
  `RESEARCH_LOG.md`.
- `pinescripts/squeeze_expansion_1h.pine` — TradingView implementation (compiles clean;
  emits buy/sell/close webhook alerts; run one per coin's 1h chart).
- Data added: `blofin_HYPE_USDT_USDT_5m_180d.parquet` (fetched).
- Reuses the honest engine `scripts/scalping/analysis/sol_strategy_2026-05-30/btengine.py`.

Reproduce: `python triage_lighter.py` → `python sweep_squeeze.py` → `python finalize_squeeze.py`.

## 11. Comparison vs prior SOL short-MR
| | Prior SOL short-MR | This squeeze basket |
|---|---|---|
| Coins | SOL only | SOL+ETH+ZEC+HYPE |
| Direction | short only | long + short |
| FULL PF | 1.50 (1 coin) | 1.76 (portfolio) |
| Generalizes? | **No** (BTC/ETH/ZEC lose) | 3–4 of 4 coins |
| t-stat | 1.51 | **2.45** |
| Walk-forward | 3/4 | 3/3 |
| Verdict | shelved | paper-trade candidate |
