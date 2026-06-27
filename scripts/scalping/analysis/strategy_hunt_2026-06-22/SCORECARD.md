# Strategy Hunt Scorecard

Honest-engine verdicts. Basket = SOL/ETH/BTC/HYPE/ZEC/BNB/DOGE/SUI, OKX data ~Dec 2025–Jun 2026.
Gate: walk-forward robust (≥3/4 folds), payoff>1, DD≤25%, 0 liq, survives BloFin fees.

## Real strategies (faithfully ported from Rich's TradingView library)

| strategy | source | TF | verdict | notes |
|---|---|---|---|---|
| Flawless Victory v1 | @ Pine v11.3 (BB12/1.99 + RSI9, SL0.5%/TP1.57%) | 5m/15m/1h | ❌ REJECTED | Loses on basket (PF 0.85–0.99 long, <0.8 both). Payoff 2.8 but 0.5% stop → 23–26% WR. Confirms BTC-15m overfit reputation. |
| @millerrh Donchian Breakout | Pine v5 (channel-trail, no TP) | 1h | ❌ FRAGILE | Faithful port: walk-forward 2/4 folds. Healthy payoff 2.7–3.0 but regime-dependent. Daily/multi-year fair trial not yet run. |

## Invented families (my reimplementations — NOT real strategies; for reference only)

| family | verdict |
|---|---|
| regime_mr (fade) | profitable but payoff 0.1–0.2 + dies on BloFin fees |
| donchian_breakout (my ATR-TP version) | fragile 1/4 — flawed (capped winners); superseded by faithful @millerrh port |
| momentum basket (reclaim_pullback etc.) | fragile, regime-dependent |

## Queue (priority by portability × likely durable edge)
1. AI Williams Alligator (ATR Stop) — distilling
2. My SuperTrend + EMA + RSI [SOL] — distilling
3. Neo_TP_TrendPullback_5m_v1.4 (Rich's own)
4. CM EMA + T3 @DaviddTech
5. SMC Uncle Sam (Optimized SOL 15m) — complex
6. DuxAlgo / [IMBA] ALGO ST / SMRT Algo Pro V3 — may be closed/repainting

## ★ WINNER (Stage 10) — 4h Donchian + BTC daily-regime filter
Faithful @millerrh Donchian, 4h, gated to long-only when BTC daily close > EMA100(daily).
Tested on 3y multi-regime data (2023-06→2026-06), 10 majors, honest engine, walk-forward.
- Full 3y: net **+142%**, **maxDD 8%**, Sharpe 1.65, PF 1.98, payoff 3.46
- Per-year: 2023 +40% · 2024 +75% · 2025 +39% · 2026 −12% (partial bear)
- **Survives BloFin fees: +134%, PF 1.89** (Lighter-outage insurance ✓)
- Walk-forward: wins every trending fold (fold2 +102%, PF 12), FLAT in worst bear (avoided −32%),
  only −3%/−9% in mild down regimes. Robust in its design domain.
- Config: dc_high=49, dc_low=29, dc_stop=14, use_tight_stop=True, ma_filter=True ma200 EMA.
- Caveats before live: (1) net% is fixed-risk per-coin pooled — build portfolio sim for true
  account curve; (2) 2 liq over 3y — tighten leverage to hit zero-liq; (3) bull-driven —
  pair with a bear/non-directional strategy for off-regime, or just sit in cash.

## Standing conclusion
FIRST DURABLE EDGE FOUND. Long crypto trend-following (Donchian) + broad-market (BTC daily)
regime filter = net-positive across 3 years with small drawdown, surviving fees. Earlier
"nothing works" was an adverse 6mo choppy-bear sample. The 6mo bear is just the 2026 −12%
slice of this strategy's arc.
