# Lighter Zero-Fee Strategy Search — Research Log (2026-05-30)

Goal: a fresh strategy for Lighter-style zero-fee perps, multi-coin (SOL/ETH/ZEC/HYPE),
both directions, 5m/15m/1h, honest no-lookahead testing, OOS + walk-forward, cross-instrument.
Do NOT reuse the prior SOL short-MR as a starting point. Pass bar: PF ≥ 1.30 after slippage,
maxDD ≤ 20%, ≥ 80 trades preferred, positive OOS, ≥ 3 WF folds majority positive.

Engine: reused the honest engine (`../sol_strategy_2026-05-30/btengine.py`) — no-lookahead,
realistic market/limit fills, stop = adverse extreme + slippage, both-hit → stop wins,
leverage-safe sizing. Lighter cost model = maker/taker 0%, slippage 0.05% on market & stop
fills only, funding 0.01%/8h.

## Families built & tested (all long + short, hard ATR stops)
range_fade · failed_breakout · sweep_reversal · squeeze_expansion · reclaim_pullback · mr_fade2

## Stage 1 — cross-instrument triage (6 families × {5m,15m,1h} × {both,long,short} × 4 coins)
One untuned param set each, full sample, per-coin PF. Findings:

| Family | Result |
|---|---|
| **squeeze_expansion 1h** | **Only family positive across coins.** Short: SOL 1.14 / ETH 1.50 / ZEC 1.57 / HYPE 1.29 (avg 1.38, 4/4). Both: avg 1.12. |
| range_fade | Dead everywhere (PF 0.6–0.9). |
| failed_breakout | Dead (0.5–0.9). |
| sweep_reversal | Dead (0.6–1.0). |
| reclaim_pullback | Mixed/weak (ETH 1h long 1.47 but SOL/HYPE <1). No consistency. |
| mr_fade2 | Only SOL 1h works (1.32) — **confirms prior SOL-specific MR; does not generalize.** |
| All 5m / 15m | Universally weak even at zero fees — slippage on stops/market entries still bites; the edges aren't there. **1h is the sweet spot.** |

## Stage 2 — squeeze 1h tuning (anti-overfit: tuned on POOLED IS across 4 coins)
Grid over bb_len, kc_mult, min_squeeze, sl_atr, tp_atr, trail, side. Ranked by pooled-IS PF.
Key observation: many configs had strong pooled OOS **carried by ZEC** while SOL/ETH collapsed
OOS — so pooled numbers alone were misleading. Drilled the leaders per-coin:

- **`both, sq10, sl1.5, tp3.0, trail`** = best balance: pooled PF 1.70, t 2.45, top-3 wins 15%
  of gross, 6/7 months positive, FULL 4/4 coins positive, OOS 3/4 positive (ETH 0.52 the miss).
- Removing the trail (`trail0`) dropped pooled PF 1.70 → 1.26 and t → 1.53 → **the trailing
  exit is the edge** (ride the expansion). Confirms it's a momentum/vol-expansion play.
- `short sq4` looked great in-sample (per-coin 1.5–1.7) but **SOL/ETH OOS collapsed to ~0.1**
  → rejected (in-sample-only).

## Stage 3 — portfolio validation of the chosen config (merged 4-coin equity)
- Lighter zero-fee: **PF 1.76, maxDD 17.3%, n 192, WR 34%, avgR +0.43, t 2.45, worst streak 11.**
- BloFin fees: PF 1.54 (maxDD 18.3%) — **not zero-fee-dependent.** Slip .10%: PF 1.54.
- IS 1.57 → **OOS 1.81** (OOS > IS). **Walk-forward 3/3 folds positive** (3.08 / 2.93 / 2.47).
- Risk sizing: 1% → DD 17.3%; **0.75% → DD 13.3%** (recommended); 0.5% → DD 9.0%.

## Verdict
**Squeeze compression→expansion, 4-coin 1h basket = paper-trade candidate that meets the pass
criteria at the portfolio level.** Clearly stronger than the prior SOL short-MR (4 coins vs 1,
both directions vs short-only, t 2.45 vs 1.51, generalizes 3–4/4, 3/3 WF, works on BloFin too).

Caveats (see STRATEGY.md §8): basket-level edge (SOL alone PF 1.11), ZEC strongest / ETH weak
OOS, correlated coins, low 34% WR fat-tail profile, OOS t only 1.30. Forward-test on Lighter
paper with the §9 kill-switch.

## Rejected, with reasons
- range_fade / failed_breakout / sweep_reversal: no edge on any coin/TF even at zero fees.
- reclaim_pullback: inconsistent across coins (one-coin pops only).
- mr_fade2: SOL-only (prior finding re-confirmed; no generalization).
- 5m & 15m for all families: slippage + weak signal → negative.
- squeeze short-only / sq4: strong IS, collapses OOS on SOL/ETH (overfit to in-sample).
- squeeze without trail: PF 1.26, not significant — the edge requires the trailing exit.
