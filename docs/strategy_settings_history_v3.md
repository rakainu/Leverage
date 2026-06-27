# V3 Strategy Settings History — Entry / Exit / Safety Rules

Recon compiled 2026-06-22. Covers the original V3.1, the V3.2 line (Reclaim entry-parity),
and every safety rule we bolted on along the way. Numbers are quoted from the actual config
files in this repo, not memory.

---

## TL;DR Evolution

```
 V3 (orig)        V3.1                    V3.2 / Reclaim
 2026-04          2026-05-15              2026-06-20
 ─────────        ──────────────────     ─────────────────────────
 enter AT          + EMA9 retest gate      + EMA9 RECLAIM (close
 signal              (wick touches 9EMA)     back across 9EMA)
 (phantom         + slope gate 0.15        + 0.05% max-gap filter
  fills)          + block Sunday            + native HA-V3 signal
 slope 0.03       + skip body 0.3-0.5         (no TV webhook)
 BloFin           + BE move + trail        + 30x, Lighter-only
                  TV webhook signal        PF 1.27 OOS-stable
                  BloFin demo
 PF ~1.03         (shut down 2026-06-20)   LIVE paper now
```

Plus a separate **Scalper** book (regime-gated VWAP MR, 15m) that carries the
3-loss cooldown, compounding, and withdrawal rules.

---

## 1. ORIGINAL V3.1  (deployed 2026-05-15, BloFin demo — now shut down)

**Source:** `scripts/scalping/v3.1-drafts/blofin_bridge.yaml`

### Entry
- **Signal:** TradingView alert via webhook (Pine HA-V3 buy/sell) → bridge re-validates locally
- **Confirmation gate:** price must **retest the 9 EMA** (5m) before the bridge fires
  - `ema_retest_period: 9`, `ema_retest_timeframe: "5m"`
  - `ema_retest_max_overshoot_pct: 0.2`  (wick may break 9EMA by ≤0.2%)
  - `ema_retest_timeout_minutes: 30`  (signal expires if no retest in 30 min)
- **Slope gate:** `min_5m_slope_pct: 0.15`  ← the V3.1 headline change (was 0.03 in V3)
- **Filters added in V3.1:**
  - `block_weekdays_utc: [6]`  → no Sunday (cost −$2,381 over 6mo)
  - `block_body_atr_band: [0.3, 0.5]`  → skip mid-body chop (cost −$3,502 over 6mo)

### Exit / Risk (baseline $100 margin — auto-scales per coin)
| Knob | Baseline | ZEC effective (2.5×) | Meaning |
|---|---|---|---|
| `sl_loss_usdt` | **$13** | **$32.50** | hard stop |
| `breakeven_usdt` | **$12** | **$30** | profit at which SL → entry (**this is your "+$20-ish BE move"**) |
| `lock_profit_activate_usdt` | $18 | $45 | enter lock state |
| `lock_profit_usdt` | $15 | $37.50 | SL locked at entry + this |
| `trail_activate_usdt` | $30 | $75 | enter trail state |
| `trail_start_usdt` | $32 | $80 | trail begins |
| `trail_distance_usdt` | **$15** | **$37.50** | SL trails peak by this |
| `tp_limit_margin_pct` | 2.0 | 2.0 | hard TP = 2× margin |

**SL ladder (4 states):** initial stop → break-even (+$12/$30) → lock profit (+$18/$45) →
trail (+$30/$75, then trails by $15/$37.50).

> Note: ZEC SL was later widened $32.50 → **$82.50** on 2026-05-23 from live evidence.
> The table above is the *original* 2026-05-15 deploy.

---

## 2. V3.2 / RECLAIM  (deployed 2026-06-20, Lighter paper — LIVE now)

**Source:** `scripts/reclaim-bridge/config.reclaim.yaml`

The "closest honest twin" of V3.2 — built after we proved the old phantom-fill entry
(filled exactly at 9EMA, non-causal) was unachievable. Reclaim is the causal version.

### Entry  (decision order)
1. **Native signal** — HA-V3 momentum flip computed in-process (`sensitivity: 8`, `fakeout: 0.2`,
   `range_filter: 0.2`). **No TradingView webhook anymore.**
2. **Touch** — bar wick reaches 9EMA (`retest_overshoot_pct: 0.2`, `retest_timeout_bars: 6`)
3. **RECLAIM** — bar must **CLOSE BACK across the 9EMA** (`require_reclaim: true`) — the key
   upgrade vs V3.1's plain retest. A breakdown aborts instead of filling.
4. **Slope gate** — `min_abs_slope_pct: 0.15`
5. **Filters** — `block_body_band: [0.3, 0.5]`, `block_weekdays: [6]`
6. **Gap filter** — `max_gap_pct: 0.05`  → skip if `|close − ema9| / ema9 > 0.05%`.
   This is the validated edge knee; edge inverts past 0.06% (sharp cliff = real, not overfit).
7. **Fire** market entry at bar close.

### Exit / Risk  ($250 margin @ 30x = $7,500 notional/coin)
| Knob | Value |
|---|---|
| `sl_loss_usdt` | **$82.50** |
| `breakeven_usdt` | **$30** (SL → entry) |
| `lock_profit_activate_usdt` | $45 |
| `lock_profit_usdt` | $37.50 |
| `trail_activate_usdt` | $75 |
| `trail_start_usdt` | $80 |
| `trail_distance_usdt` | $37.50 |
| `tp_ceiling_pct` | 2.0 (= $500 hard TP) |

Same 4-state SL ladder shape as V3.1, just with the ZEC-scale dollar values baked in.

- **Coins (7):** BTC, SOL, DOGE, XRP, HYPE, BNB, ZEC — $250 each, fixed (no compounding)
- **Leverage:** 30x  •  **Collateral:** $3,600  •  **Venue:** Lighter only (BloFin fees kill it)
- **Validation:** PF 1.27 OOS-stable (IS 1.28 / OOS 1.27), maxDD −$292

---

## 3. SAFETY RULES (the stuff we added over time)

### 3a. 3-Loss Cooldown breaker  — *Scalper book*
**Source:** `scripts/scalper-bridge/config.scalper.yaml`
```yaml
cooldown:
  enabled: true
  consec_losses: 3     # 3 consecutive losers (pooled across basket)
  minutes: 180         # block ALL entries for 3h, then auto-resume
```
Backtest: PF 1.49 → 1.58 (OOS 1.42 → 1.47), only ~4% fewer trades. Built to stop
mean-reversion shorts bleeding into news rips. Revert = `enabled: false`.
*(Rich tuned this from an initial 2-loss / 360-min version that was too twitchy.)*

### 3b. Break-even SL move
The `breakeven_usdt` knob in every V3 config — moves SL to entry once peak profit
crosses the threshold ($12/$30 in V3.1, $30 in Reclaim). First rung of the SL ladder.

### 3c. Kill switch / entry gate  (Telegram)
`scripts/scalping/src/blofin_bridge/entry_gate.py` — per-symbol pause/resume:
- `/off <SYM>` pause entries • `/on <SYM>` resume • `/close <SYM>` flatten + pause • `/status`
- In-memory; restart resets to running.

### 3d. Compounding sizing  — *Scalper book*
```yaml
sizing: { mode: compound, base_equity: 3600, cap_mult: 3.0 }
```
Margin scales with equity, capped at 3× ($1,500/coin = $15k notional), floors to 0 on
drawdown (de-risks automatically). Revert = `mode: fixed`.

### 3e. Weekly withdrawal skim  — *Scalper book*
```yaml
withdrawal: { enabled: true, cadence: weekly, target_mult: 3.0 }
```
Skims realized equity above $10,800 (3,600 × 3) once per ISO week. Never touches open P&L.
Revert = `enabled: false`.

### 3f. News-rip / climax guards  — *Scalper book*
- `accel_atr: 3.0` — skip fading bars where High−Low ≥ 3×ATR (climax bars)
- `min_slope_pct: 0.08` — require trend clarity before fading against EMA200

---

## 4. SCALPER (regime-gated VWAP MR) — for reference

Separate book, **not** a V3 descendant. 15m, `scripts/scalper-bridge/config.scalper.yaml`.
- Entry: EMA200 trend gate + z-score ≥1.5 fade vs session VWAP, maker limit at close ±0.25×ATR
- Exit: SL 2.0×ATR, TP 0.3×dist-to-VWAP, time stop 12 bars (3h)
- 8 coins, $500 margin @ 10x, PF 1.49 / 89% WR. Carries all the safety rules in §3.

---

## 5. Side-by-side: Entry & SL across versions

| | V3 (orig) | V3.1 | V3.2 / Reclaim |
|---|---|---|---|
| Signal | TV alert | TV alert | native HA-V3 (no TV) |
| Entry gate | none (fill at 9EMA) | **9EMA retest** | **9EMA reclaim (close-back)** |
| Gap filter | — | — | **0.05% max gap** |
| Slope gate | 0.03 | **0.15** | 0.15 |
| Sunday block | no | **yes** | yes |
| Body-chop skip | no | **[0.3,0.5]** | [0.3,0.5] |
| Hard SL | varies | $13 / $32.50 (ZEC) | $82.50 |
| Break-even move | — | +$12 / +$30 (ZEC) | +$30 |
| Trail distance | — | $15 / $37.50 (ZEC) | $37.50 |
| 3-loss cooldown | no | no | (Scalper book only) |
| Venue | BloFin | BloFin demo | Lighter |
| Result | PF ~1.03 (phantom) | shut down 06-20 | PF 1.27 OOS, LIVE |

---

*Files: `scripts/scalping/v3.1-drafts/blofin_bridge.yaml` (V3.1) ·
`scripts/reclaim-bridge/config.reclaim.yaml` (V3.2) ·
`scripts/scalper-bridge/config.scalper.yaml` (safety rules).*
