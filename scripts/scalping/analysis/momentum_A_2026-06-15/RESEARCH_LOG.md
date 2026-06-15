# Aggressive 15m day-trade hunt — A (momentum) vs B (proven MR) — 2026-06-15

**Target (Rich):** ~2x in 1–2 months, ~40% max drawdown tolerated, $3k, on Lighter.
A momentum/breakout day-trade strat to accompany the scalper; if A fails → B (crank a
proven edge). "Give A a real chance, no fear-based conservatism."

**Data:** fresh OKX 15m through Jun 15, broad basket (AVAX BNB BTC DOGE ETH HYPE LINK
SOL ZEC + ZEC via 5m resample). Honest btengine, Lighter zero-fee.

## A — momentum/breakout: DEAD (thoroughly tested)
5 families (donchian, vol_expansion, roc_momentum, ema_momentum, pullback_trend),
100+ configs, breakout-chase AND buy-the-dip, full basket AND trender subset
(ZEC/DOGE/AVAX/HYPE/SOL), best-case zero fees:
- **Everything net-negative.** Best = donchian on trenders avgR −0.006 / PF 0.95 (still
  losing). Win rates stuck 22–28% — breakouts get faded.
- Per-coin: only ZEC/DOGE/AVAX show small positives; majors (BTC/ETH/BNB) bleed hard;
  no config generalizes (≥60% coins +).
- **Structural reason:** 15m crypto MEAN-REVERTS, doesn't trend. Same fact that makes
  the scalper (fades extensions) win and momentum (chases them) lose. Zero fees can't
  fix a negative-expectancy entry. → A does not clear. Move to B.

## B — regime_mr (proven edge) sized aggressively: CLEARS
Same regime-gated VWAP mean-reversion family as the live scalper (sl 2.0ATR, tp 0.3·
dist-to-vwap, 12-bar stop, maker entry), on fresh 15m, Lighter 0-fee:
- **Edge replicates on fresh data:** 5-coin PF 1.47 / 8-coin PF 1.33, WR 88%, ~180
  trades/wk, survives 2× slippage (PF 1.39). Matches the original validation.
- **Pace (small-account, before liquidity bites):** 5-coin 1% risk → maxDD 15%, ~8wk to
  2x; 2% risk → maxDD 27%, ~4wk to 2x; 3% → 38% DD, ~3wk. Comfortably inside the 40% DD
  budget at 1–2% risk.
- **CAVEAT — absolute $ figures are fantasy.** Compounding with infinite-liquidity
  assumption produces $M–$B finals that Lighter depth could never fill. Real growth
  FLATTENS as the account scales (can't compound a large book at small-account %). The
  honest, defensible claim is only: *doubling $3k in ~4–8 weeks within ~15–27% DD is
  credible*; beyond that, size/liquidity caps returns.
- **Honest framing:** B is the SCALPER'S edge run hot — not a new diversifier. A genuine
  *different* momentum strat failed; the profitable aggressive day-trade play at this
  scale IS mean-reversion.

**Decision pending (Rich):** (a) crank existing scalper vs stand up a 2nd aggressive
book; (b) risk appetite — 1% (~15% DD, ~8wk/2x) vs 2% (~27% DD, ~4wk/2x). Then build a
liquidity-realistic sizing model (notional cap reflecting Lighter depth) before any deploy.

Files: fetch_basket.py, momentum_lib.py, sweep_stage1.py/1b.py, sweep_diag.py,
b_feasibility.py. NOT deployed.
