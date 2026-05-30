# SOL Leverage Strategy — Research Log (2026-05-30)

Goal: a profitable SOL leverage strategy for BloFin on 5m / 15m / 1h that passes
strict backtesting (PF ≥ 1.30, maxDD < 20%, ≥ 80 trades, positive OOS) after fees
and slippage, with a real hard stop and OOS/walk-forward validation.

All numbers are from the honest Python engine in this folder: no-lookahead (signal
on bar close, execute next bar), market = next-open + slippage, limit = fill only
if a later bar trades through, stop = adverse bar extreme + slippage (taker), TP =
maker limit, both-hit bar → stop wins, one position at a time. BloFin cost model:
taker 0.06% / maker 0.02% / slippage 0.05% / funding 0.01% per 8h. Engine pinned by
`test_engine.py` (13 honesty unit tests, all green).

## What was tried, in order

| # | Experiment | Result | Verdict |
|---|---|---|---|
| 1 | Triage: 4 families (Donchian, z-fade, EMA-pullback, ADX-breakout) × {5m,15m,1h}, default params, full sample | Every config loses. PF 0.40–0.99. Best = z-fade 1h ~breakeven (PF 0.98–0.99). | Naive params all dead after costs. MR closest. |
| 2 | MR sweep (864 configs) IS(70%)/OOS(30%) on **5m** | Best IS PF 0.89, all negative; OOS PF 0.42–0.55. | **5m dead** — cost/move ratio too high. |
| 3 | MR sweep on **15m** | Best IS PF 1.11; no robust positive cluster; OOS mostly negative. | **15m dead** — no edge survives. |
| 4 | MR sweep on **1h** | Robust positive neighborhood: z=20, z_entry=2.5, ADX≤35, sl 2.0–2.5, tp=mean. IS PF 1.13–1.21, **OOS PF 1.23–1.34** (OOS often > IS). | **1h has a real MR edge.** Ranking by top-IS-PF alone overfits (knife-edge z=2.0/ADX=25 fail OOS); the *neighborhood* is the robust signal. |
| 5 | 1h MR side/trend/regime refinement | **Edge is SHORT-ONLY.** Shorts (fade +2.5σ rips): FULL PF **1.50**. Longs (buy −2.5σ dips): PF **0.77** (loses). Both-sides: PF 1.23 (longs drag down shorts). | Counter-intuitive but consistent: SOL +2.5σ blow-offs revert; −2.5σ dips cascade. |
| 6 | Push short-only to ≥80 trades (loosen z_entry / widen ADX) | Every n≥80 config drops to FULL PF 1.10–1.18, OOS ~1.00. | Can't have both ≥80 trades **and** PF≥1.30. The extra trades are net-negative. Edge lives only in the extreme. |
| 7 | Fragility audit of short-only winner | Not outlier-driven (top-3 wins = 16% of gross). 4/6 months positive (others flat). Survives harsh costs (taker .10/maker .05/slip .15 → PF **1.32**). Stable plateau on sl_atr (PF 1.21–1.52 across 1.5–3.0) and ADX (30/35/40 all positive). | Structurally robust on SOL. |
| 8 | **Cross-instrument** (same config, no re-tuning) BTC/ETH/ZEC 1h | SOL 1.50; **BTC 0.89, ETH 0.75, ZEC 0.55 — all lose.** Pooled SOL+BTC+ETH PF 1.00 (breakeven). | **Does NOT generalize.** Strong evidence the SOL edge is partly regime-specific / curve-fit. |
| 9 | Significance | FULL t-stat 1.51, OOS t-stat 0.89 (both < 2.0). | Sample too small to reject "noise" at 95%. |
| 10 | Trend-following IS/OOS sweep on 1h (Donchian, ADX-breakout) | Textbook overfit: ADX-breakout IS PF 1.5–1.83 → **OOS PF 0.29–0.50**. Donchian best OOS 1.17 (n=22), fails cross-instrument. | **No robust trend edge** on SOL 1h. |

## Bottom line

The **SOL 1h short-only z-score mean-reversion** strategy is the single best candidate.
On SOL it clears the headline thresholds — PF 1.50 (≥1.30), maxDD 4.4% (<20%),
OOS PF 1.47 (positive), survives 2–3× cost stress — with a clean, low-parameter,
non-outlier, multi-month-positive profile.

It does **not** clear the bar on three linked dimensions:
1. **59 trades < 80** (and forcing ≥80 collapses PF below 1.30).
2. **t-stat 1.51 < 2** — not statistically significant at 95%.
3. **No cross-instrument generalization** — same rules lose on BTC/ETH/ZEC.

Honest conclusion: this is a **promising, fully-specified hypothesis**, not a proven
edge. It is good enough to **paper-forward-test on BloFin** (which is the goal), with
a kill-switch — paper trading is precisely how the statistical-power gap gets resolved.
No martingale / grid / averaging-into-losers anywhere; single hard ATR stop per trade.

### Best FAILED candidates (for the record)
- **Both-sides 1h MR** (z2.5/ADX35/sl2.0): PF 1.23, n=100, maxDD 6.4%, OOS PF 1.27, WF 4/4 positive. *Meets ≥80 trades + DD + positive-OOS, fails PF≥1.30.* Highest-frequency fallback.
- **Long-only 1h MR**: PF 0.77 — fading SOL dips loses outright.
- **5m / 15m MR**: dead (PF ≤ 1.11, OOS negative).
- **1h trend-following**: overfit (OOS PF 0.29–0.50).
