# Research Log — Aggressive Scalping Edge Search (2026-05-30)

Goal: find a high-FREQUENCY scalping edge for Lighter zero-fee perps. Not a
rare-signal strategy. Test aggressive ideas honestly; backtest is the filter.

## Pipeline
- `fetch_okx.py` — OKX data (BloFin geo-blocked this IP; Binance/Bybit 451; Gate
  caps 1m to 7d). 5 coins, 1m/3m/5m, parallel per-coin, zero gaps. `data/`.
- `strat_lib.py` — 17 families (incl. new `regime_mr`). Reuses btengine
  (sol_strategy_2026-05-30) + the 6 lighter_strat families.
- `common.py` — loader + runner + Lighter/hi-slip/BloFin cost models.
- `stage1_triage.py` — 320 combos, defaults, side=both → `runs/stage1.json`.
- `stage2_sweep.py` — curated targets, full limit-entry grids → `runs/stage2_curated.json`.
- `stage3_validate.py` — single-config IS/OOS/WF/slippage/leverage.
- `basket_validate.py` — multi-coin pooled IS/OOS/WF/slippage/notional.

## Key results
- TF gradient (untuned market entry, mean avg_r): 1m −0.885, 3m −0.354, 5m −0.279,
  15m −0.158. **1m untradeable after slippage; edge zone = 15m.**
- Maker/limit entry is the lever (removes entry slippage; zero-fee ⇒ stops only
  slipped cost).
- vwap_revert HYPE 15m long: PF 1.26, 30/wk, WF 4/4 — but **HYPE-only** (others
  0.80–1.03), HYPE buy&hold +126% ⇒ directional/regime artifact.
- mr_fade2 3m/5m: overfit (HYPE 5m OOS PF 0.80; ZEC 3m WF fold 0.22). Rejected.
- rsi_snapback SOL 15m both: PF 1.50, but 6/wk, SOL-specific, 1 losing WF fold.
- **regime_mr basket (WINNER):** fade VWAP extensions WITH the EMA200 trend, 5-coin
  15m. Tuned config stop 2.0·ATR / TP 0.3×dist: pooled **192/wk, PF 1.49, 89% WR**,
  exp +0.061%/trade, hold 14m. **All 5 coins profitable** (1.33–1.74). IS 1.52 /
  OOS 1.42. WF 1.42/1.46/1.63/1.49. 2× slip → 1.43. 10x = 0 liq; $250@10x =
  +$756/26wk, maxDD $54. (Base sl1.5/tp0.4 was 186/wk PF 1.22 81% WR.)
- ADX/trend-strength gating cut frequency without lifting PF — chop isn't the main
  driver; edge is thin (breakeven WR ≈ 84%, ~4.7pt cushion vs 88.5% actual).
- **Exit management (engine extended, regression-clean):** breakeven-trail is inert
  (holds ~1 bar, TP ≪ stop so +1R BE trigger never reached); partial-TP/runner
  HURTS (PF→1.07 — MR move done at VWAP, no trend to harvest). PF lift came from
  stop/TP GEOMETRY, not exit mgmt.

## Honest verdict
No cross-coin-generalizing 3m/5m scalp edge exists in this data. The real edge is
regime-gated 15m mean reversion (generalizes, high-freq, high-WR, PF 1.22 — just
under the 1.25 preferred). Recommended for paper with strict WR/PF kill-switches.

## Engine fixes (sanctioned)
- sweeps/2026-05-20/engine.py: killed fetch infinite-retry loop, added
  Cloudflare-aware backoff + consecutive-failure cap, page_size 100→1000.
