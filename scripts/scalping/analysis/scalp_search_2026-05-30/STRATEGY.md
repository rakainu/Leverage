# Aggressive Scalping Edge Search — Findings (2026-05-30)

**Venue model:** Lighter-style zero-fee perps (taker/maker = 0%, slippage 0.05% on
market & stop fills, funding 0.01%/8h). All fills honest: signal on bar close,
work next bar; maker limits fill only if a later bar trades through; stop wins on
both-hit bars; one position at a time; no lookahead/repaint.

**Data:** OKX USDT-perps (only reachable venue with deep history + all coins;
BloFin geo-blocks this IP, Binance/Bybit 451, Gate caps 1m to 7d). Single
consistent source, zero gaps. 1m=45d, 3m=120d, 5m=180d, 15m resampled from 5m.
Coins: SOL, ETH, ZEC, HYPE, BTC.

**Scope:** 17 strategy families × 5 coins × {1m,3m,5m,15m} × {long,short,both},
maker & market entries. Stage 1 triage (320 combos) → Stage 2 grid (~9k honest
backtests) → Stage 3 validation (IS/OOS, 4-fold walk-forward, 2× slippage,
leverage/notional).

---

## Headline conclusions (honest)

1. **Market-entry scalping does not survive slippage.** Mean expectancy by
   timeframe (untuned, market entry): **1m −0.885 R, 3m −0.354, 5m −0.279,
   15m −0.158.** 1m is hopeless — 5 bps slippage on every fill dwarfs the move.
   The viable zone is 15m; 3m/5m produced no *stable* edge even with maker entries.

2. **The edge lives in maker/limit entries + mean reversion.** Zero-fee + maker
   entry removes entry slippage entirely (stops are the only slipped cost). Every
   surviving candidate is a maker-entry mean-reversion family.

3. **Single-cell "winners" are mostly regime/coin artifacts.** `vwap_revert HYPE
   long` looked great (PF 1.26, 4/4 WF) but **fails on the other 4 coins** — HYPE
   buy&hold was **+126%** over the window, so it's largely a long-the-uptrend bet.
   3m/5m `mr_fade2` cells were overfit (HYPE 5m OOS PF 0.80; ZEC 3m WF fold PF 0.22).

4. **The one edge that GENERALIZES is regime-gated mean reversion** — fade
   extensions back to session VWAP **only in the direction of the higher-timeframe
   trend**, across a 5-coin basket. Profitable on all 5 coins, OOS > IS, 4/4
   walk-forward, survives 2× slippage. This is the recommended candidate.

---

## Top 3 candidates

### #1  regime_mr basket — RECOMMENDED  (regime-gated VWAP mean-reversion)
- **Rule:** On 15m, trend = sign of EMA(200) slope over 20 bars. Compute z-score
  of (Close − sessionVWAP) over 30 bars. **Uptrend + z ≤ −1.5 → long; downtrend +
  z ≥ +1.5 → short.** Maker limit entry 0.25·ATR beyond close. Hard stop 1.5·ATR.
  TP = 0.4 × (distance to VWAP), maker. Time stop 12 bars (3h). Both sides.
- **Coins/tf:** SOL+ETH+ZEC+HYPE+BTC, 15m, basket (one position per coin).
- **Frequency:** **186 trades/wk pooled (~27/day)**; 35–39/wk per coin.
- **Win rate:** **81%** (79–82% every coin).
- **Profit factor:** **1.22** pooled (per coin 1.05–1.40).
- **Expectancy:** +0.037%/trade after slippage; avg win +0.26%, avg loss −0.88%.
- **Avg hold:** 18 min.
- **Max DD:** $92 on $250 notional @10x over 26 wk (engine risk-sized DD ~10–33%/coin).
- **OOS/WF:** IS PF 1.20 / **OOS PF 1.28**; folds 1.19 / 1.08 / 1.34 / 1.36 (4/4 +).
- **Slippage:** 2× (0.10%) → PF **1.15** (still profitable).
- **Best leverage/notional:** **10x is clean (0 liquidations).** 20x → 3 liq /4779
  (ok). 30x → 21 liq (avoid). $250 @10x → **+$443 / 26 wk**.
- **Why it passes:** generalizes across all coins, robust OOS+WF+slippage, true
  high-frequency scalp cadence, 81% WR meets "prioritize win rate."
- **Risk to watch:** breakeven WR ≈ 77% (loss is 3.4× win). Only ~4 pts of cushion
  — a chop/whipsaw regime that decays WR below ~78% flips it negative. Hence the
  kill-switch below. PF 1.22 is just under the 1.25 preferred minimum.

### #2  vwap_revert HYPE 15m long  (higher-octane, single-coin, directional)
- **Rule:** z-score of (Close − sessionVWAP) over 30 bars ≤ −1.5 → long. Maker
  limit 0.25·ATR below close, stop 1.0·ATR, TP 0.5×dist-to-VWAP, time stop 12 bars.
- **HYPE only, long only.** Freq **30.6/wk**, WR 65% (WF up to 78%), PF **1.26**,
  exp +0.072%/trade, hold 28m. WF 4/4 (+). Slippage 2× → PF 1.18. 10–20x safe.
- **Why limited:** edge is substantially the HYPE uptrend (+126% buy&hold). Does
  NOT generalize (SOL 0.96 / ETH 0.90 / ZEC 1.03 / BTC 0.80). Long-only =
  regime-dependent. Tradeable as an aggressive HYPE bet *with a hard kill-switch*,
  not a market-neutral alpha.

### #3  rsi_snapback SOL 15m both  (quality over quantity, both-sided)
- **Rule:** RSI(14) crosses back up through 20 → long; back down through 70 →
  short. Maker limit 0.25·ATR, stop 1.0·ATR, TP 1.0·ATR, time stop 12 bars.
- **SOL, both sides.** Freq **6.4/wk**, WR 62%, PF **1.50**, exp +0.119%/trade,
  hold 35m. OOS PF 1.83. WF 3/4 (+) (one losing fold, n=10). Slippage 2× → PF 1.39.
- **Why limited:** lowest frequency (below the 5–20/wk comfort band), SOL-specific
  (only 1–2/5 coins generalize). Highest-quality single edge but thin cadence.

**Rejected with evidence:** all 1m (slippage-killed); 3m/5m mr_fade2 (overfit,
OOS/WF fail); reclaim_pullback ZEC 15m (PF 1.27 but 22% WR, 3–4.5h holds, 50% DD —
a trend-rider, not a scalp); plain both-sided MR (0/5 coins generalize).

---

## Final recommendation — paper trade #1 (regime_mr basket)

**Exact settings (per coin, run all 5 concurrently):**
```
timeframe        : 15m
coins            : SOL, ETH, ZEC, HYPE, BTC   (independent, 1 position each)
trend filter     : EMA(200) slope over 20 bars; long only if slope>0, short only if slope<0
trigger          : z = zscore(Close - sessionVWAP, 30);  long z<=-1.5 / short z>=+1.5
entry            : maker LIMIT 0.25*ATR(14) beyond close (cancel if unfilled in 3 bars)
stop             : 1.5*ATR(14) hard (taker)
take profit      : 0.4 * (entry distance to VWAP), maker limit
time stop        : 12 bars (3h) -> market close
sizing           : fixed $200-250 notional per position
leverage         : 10x  (0 liquidations in backtest; 20x acceptable)
```

**Kill-switch rules (critical — the edge is thin above breakeven WR):**
- Rolling 100-trade win rate **< 76%** → halt (below breakeven, edge gone).
- Rolling 30-day PF **< 1.05** → halt and review.
- Any single coin's 50-trade WR **< 70%** → drop that coin from the basket.
- Account drawdown **> 8%** → halt (backtest 26-wk maxDD was ~3.7% of a $1k-risk
  account; >8% means regime break).
- Daily loss **> 3R** → stop for the day.

**What to optimize next (in priority order):**
1. **Lift PF above 1.25** without overfitting: per-trade exit management (trail to
   breakeven after +0.5R; partial TP) to widen the WR cushion.
2. **Chop filter that actually helps** — ADX gating cut frequency without lifting
   PF; test a realized-vol or VWAP-band-width regime filter instead.
3. **Down-regime stress** — the window was net bullish; pull a bear/chop slice
   (or wait for one in paper) and confirm the short side carries its weight.
4. **Cross-sectional parameter optimization** — maximize the *worst-coin* PF
   rather than pooled, to harden generalization.
5. Re-confirm on BloFin/Lighter native bars once API access is sorted (OKX was a
   proxy; majors are arb-tight so the difference is negligible — verify first).
