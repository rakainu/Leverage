# SOL 1h Short-Only Mean-Reversion — Strategy Package (2026-05-30)

**One-line:** Short SOL when price spikes ≥ 2.5σ above its 20-bar mean during a
non-trending regime (ADX ≤ 35), cover at the mean, hard ATR stop. 1h chart, BloFin.

> **Status: best candidate found — clears SOL headline backtest thresholds, but is
> NOT a statistically-proven edge.** Deploy to PAPER only, as a forward-test with a
> kill-switch. See "Honest verdict" below. All metrics are net of BloFin fees +
> slippage + funding from the honest engine in this folder.

---

## 1. Final strategy rules

**Market / timeframe:** SOL-USDT perpetual, **1-hour** candles, BloFin.
**Direction:** **SHORT ONLY.** (Long mean-reversion loses on SOL — PF 0.77.)

**Indicators (all on closed 1h bars):**
- `basis = SMA(close, 20)`, `dev = stdev(close, 20)`, `z = (close − basis) / dev`
- `ATR(14)`, `ADX(14)`

**Entry (one position at a time, no pyramiding):**
- Signal when **`z ≥ 2.5`** AND **`ADX ≤ 35`**.
- Place a **maker limit short at the signal bar's close**. If not filled by the next
  bar trading up to it, the order still rests; cancel if unfilled after a few bars.

**Exits (defined at entry):**
- **Hard stop:** `entry + 2.0 × ATR(14)` (above entry). Real, always-on.
- **Take-profit:** the **20-bar mean (`basis`)** — full mean reversion (maker limit).
- **Time stop:** close after **48 bars** (48h) if neither SL nor TP hit.
- Stop-wins on any bar that touches both.

**Position sizing (fixed-fractional risk):**
- Risk **1% of equity** to the hard stop per trade.
- `notional = (0.01 × equity) / (stop_distance / entry)`.
- **Leverage cap 20×**; size so liquidation sits ≥ 2.5× the stop distance away
  (liquidation never triggers before the stop). Effective leverage ran 15–20× in
  backtest with **0 liquidations**.

---

## 2. Backtest summary (SOL 1h, 180d: 2025-12-01 → 2026-05-30)

| Metric | FULL | In-sample (70%) | Out-of-sample (30%) |
|---|---|---|---|
| Profit factor | **1.50** | 1.33 | **1.47** |
| Win rate | 59% | 56% | 58% |
| Avg R / trade | +0.227 | +0.159 | +0.220 |
| Net return (1% risk, compounding) | **+14%** | +5% | +5% |
| Max drawdown | **4.4%** | 4.4% | 4.1% |
| Trades | 59 | 34 | 24 |
| Worst losing streak | 4 trades (−$45 on $1k) | — | — |
| Largest win / loss | +$22 / −$13 | — | — |

- **Walk-forward (4 folds):** test-fold PFs 1.06 / 1.22 / 1.81 / 1.26 — **all positive**.
- **Cost stress:** harsh costs (taker .10% / maker .05% / slip .15%) → **PF 1.32**; all-taker + .10% slip → PF 1.37. Edge survives well beyond base assumptions.
- **Profit concentration:** top-3 wins = 16% of gross profit (not outlier-driven).
- **Monthly:** positive in 4/6 months, flat (−$6) in the other 2.

**Pass check vs strict criteria:** PF ✅ (1.50) · maxDD ✅ (4.4%) · positive OOS ✅
(1.47) · real hard stop ✅ · no martingale/grid ✅ · **trade count ❌ (59 < 80)**.

---

## 3. Honest verdict — read before trading

This strategy is a **hypothesis to forward-test, not a certainty.** Three findings
keep it short of "proven":

1. **59 trades < 80.** It only fires on extreme +2.5σ rips in calm tape (~1 trade /
   3 days). Loosening the threshold to reach 80 trades dropped PF to ~1.10–1.18, so
   the extra trades are net-negative — forcing the count would break the strategy.
2. **t-stat 1.51 (< 2).** With 59 trades and high R-variance, the per-trade edge is
   not statistically distinguishable from luck at 95% confidence.
3. **No cross-instrument generalization.** The identical rules lose on BTC (0.89),
   ETH (0.75), ZEC (0.55). A structural edge should appear at least weakly elsewhere;
   its absence means the SOL result may be partly regime-specific to this window.

**Implication:** paper-trade it to gather independent forward samples. **Go/no-go
rule:** after the first **30 paper trades**, keep it only if paper **PF ≥ 1.25** and
**maxDD < 12%**; otherwise kill. Never size it as a proven edge.

---

## 4. Risk settings for BloFin paper trading

| Setting | Value |
|---|---|
| Account | BloFin **demo** (`demo-trading.blofin.com`, `BLOFIN_ENV=demo`) |
| Symbol / TF | SOL-USDT perp, 1h |
| Direction | Short only |
| Margin mode | Isolated |
| Leverage | **20×** (cap; sizing makes effective lev 15–20×) |
| Risk per trade | 1% of equity to the hard stop |
| Starting paper equity | $1,000 |
| Hard stop | entry + 2.0 × ATR(14) — **mandatory, set on every order** |
| Take-profit | 20-bar SMA (mean) |
| Time stop | 48h |
| Max concurrent positions | 1 |
| Daily loss limit (circuit breaker) | −3% equity → pause to next day |
| Kill-switch | < PF 1.25 or DD > 12% after 30 paper trades |

---

## 5. Exact next steps to paper-trade

1. **Visual confirm in TradingView.** Add `pinescripts/sol_1h_short_mr.pine` to a
   SOL-USDT 1h chart, run the Strategy Tester, eyeball that shorts fire on +2.5σ
   spikes in green (range) zones and cover at the blue mean line. (TV fills won't
   match the Python engine exactly — the Python engine is the source of truth.)
2. **Bridge support (no live changes without Rich's go).** The strategy emits its
   **own SL/TP** in the webhook payload, so it needs an entry handler that accepts a
   `sell` with `sl`/`tp`/`type:limit`. This differs from the Pro V3 path — do **not**
   reuse Pro V3's no-ATR-overlay rule here; this is a self-contained strategy that
   owns its exits. Decide: (a) add a small `sol_1h_short_mr` route to the paper
   bridge, or (b) run it on the Lighter zero-fee paper bridge first (edge is larger
   with zero fees). Recommend (b) for the cheapest, fastest forward-test.
3. **Create the TradingView alert** (message below) on the strategy, "Once Per Bar
   Close", webhook URL pointing at the chosen paper bridge.
4. **Forward-test ≥ 30 trades** (~3 months on 1h, or run on Lighter paper to speed
   up), then apply the go/no-go rule.

---

## 6. TradingView alert message

The Pine script already attaches per-order JSON via `alert_message`. Set the alert's
webhook message to `{{strategy.order.alert_message}}` so entry and exit payloads pass
through automatically. Entry payload looks like:

```json
{"strategy":"sol_1h_short_mr","symbol":"SOL-USDT","action":"sell","type":"limit","price":<close>,"sl":<close + 2*ATR>,"tp":<SMA20>,"tf":"1h"}
```

Exit payload: `{"strategy":"sol_1h_short_mr","symbol":"SOL-USDT","action":"close"}`

---

## 7. Files (reproducible)

- `btengine.py` — honest engine · `test_engine.py` — 13 honesty tests
- `strategies.py` — strategy library (`mr_fade` is the candidate)
- `triage.py`, `sweep_mr.py`, `validate.py`, `refine.py`, `sweep_short.py`,
  `analyze_winner.py`, `sweep_trend.py` — the full experiment chain
- `runs/` — saved sweep outputs · `RESEARCH_LOG.md` — what passed/failed and why
- `../../../pinescripts/sol_1h_short_mr.pine` — TradingView implementation

Reproduce headline: `python validate.py` then `python analyze_winner.py`.
