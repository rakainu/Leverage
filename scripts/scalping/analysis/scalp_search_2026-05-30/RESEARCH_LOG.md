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
  15m. Pooled 186/wk, PF 1.22, 81% WR, exp +0.037%/trade, hold 18m. **All 5 coins
  profitable** (1.05–1.40). IS 1.20 / OOS 1.28. WF 1.19/1.08/1.34/1.36. 2× slip →
  1.15. 10x = 0 liq; $250@10x = +$443/26wk, maxDD $92.
- ADX/trend-strength gating cut frequency without lifting PF — chop isn't the main
  driver; edge is inherently thin (breakeven WR ≈ 77%, ~4pt cushion).

## Honest verdict
No cross-coin-generalizing 3m/5m scalp edge exists in this data. The real edge is
regime-gated 15m mean reversion (generalizes, high-freq, high-WR, PF 1.22 — just
under the 1.25 preferred). Recommended for paper with strict WR/PF kill-switches.

## Engine fixes (sanctioned)
- sweeps/2026-05-20/engine.py: killed fetch infinite-retry loop, added
  Cloudflare-aware backoff + consecutive-failure cap, page_size 100→1000.
