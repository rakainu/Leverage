# Apex — Strategy Design Spec

**Date:** 2026-06-23
**Author:** Rich (rakainu) + Claude
**Status:** Approved for planning

Apex is Rich's personal V3. It is a standalone copy of the original V3.1 scalping
bridge — TV alert → 9 EMA retest entry → fixed-dollar SL → breakeven → dollar-trail —
rebuilt to run on Lighter (zero-fee), with a simplified 3-stage exit ladder and Rich's
own starting settings. This is **not** a "find the best settings" build. The goal is to
reproduce exactly how Rich trades it; tuning comes later, only at Rich's direction.

---

## 1. Goals & Non-Goals

**Goals**
- A fully self-contained strat (`Apex`) that shares **no code, config, DB, or UI** with
  any existing strat — no repeat of the dashboard-contamination incident where a shared
  `/docker/lighter-dashboard` dir meant one UI edit hit three strats.
- Reproduce the V3.1 entry behavior: SMRT Pro V3 signal, wait for the 9 EMA retest.
- Reproduce the breakeven-then-dollar-trail exit, simplified to Rich's exact 3 stages.
- Run on Lighter (zero-fee), 3 coins, Rich's fixed sizing.

**Non-Goals (explicitly out of scope for this build)**
- No daily profit target / auto-pause behavior.
- No parameter tuning, optimization, or sweeps.
- No Sunday block (Apex trades Sundays).
- No new UI yet — that is a separate, later project. This build leaves a clean,
  isolated dashboard directory but does not build the dashboard.

**In scope (added 2026-06-23):**
- **3-loss cooldown breaker:** after 3 consecutive losing closes (basket-wide),
  block ALL new entries for **60 minutes**, then auto-resume. Uses the existing
  `CooldownConfig` + `_register_close` / `_maybe_notify_cooldown_resume`. One code
  fix required: the trail-mode close path does not currently feed `_register_close`
  (only the regime path does) — Apex must wire it in.
- **Telegram pause/stop control:** `@apexbot` accepts `/off`, `/on`, `/close`,
  `/status`, and kill-all to pause or stop trading on demand. Uses the existing
  `TelegramControl` (`control.telegram_enabled: true`). Bot token supplied via the
  `TELEGRAM_BOT_TOKEN` env var (NEVER committed — `.env` only).

---

## 2. Naming & Isolation

Everything namespaced to `apex`, nothing reaching back into `scripts/scalping/`:

| Thing | Value |
|---|---|
| Source dir | `scripts/apex/` |
| Python package | `apex_bridge` (renamed off `blofin_bridge`) |
| Config file | `scripts/apex/config.apex.yaml` |
| SQLite DB | `apex.db` |
| Container | `apex-bridge` |
| Telegram bot | `@apexbot` (new token) |
| Dashboard dir | `/docker/apex-dashboard/` (created, not populated this build) |
| Webhook path | `/webhook/apex` (own secret) |
| Domain | `apex.agentneo.cloud` (Traefik file-provider, own router) |
| Telegram bot | `@apexbot` — token in `.env` via `TELEGRAM_BOT_TOKEN`, never committed |

**Isolation rule:** Apex imports nothing from `scripts/scalping/` or any other bridge.
The Lighter execution layer is copied in (from the proven Reclaim/Scalper bridges), not
imported. A grep for cross-package imports is part of acceptance.

---

## 3. Signal & Entry

- **Signal source:** SMRT Pro V3 indicator on TradingView (Heikin-Ashi smoothed-EMA
  flip, `sensitivity=8`, `fakeout=0.2`, `range=0.2`), unchanged from V3.1.
- **Transport:** TV alert → webhook (`/webhook/apex`) → Apex queues a **pending signal**.
  The alert is exchange-agnostic (just an HTTP POST); the venue swap is downstream of it.
- **Symbol mapping:** the webhook layer maps the incoming TV ticker (e.g. `ZECUSDT.P`)
  to the Lighter market (`ZEC`) via a small mapping table — copied from the existing
  Lighter bridges, not new design.
- **Alert hygiene:** 3 fresh TV alerts (HYPE/SOL/ZEC) created at wire-up. TV alerts can
  silently expire (plan tier / inactivity) — check the alerts panel first when diagnosing
  any "no fills."
- **Entry gate — 9 EMA retest:**
  - `ema_retest_period: 9`, `ema_retest_timeframe: "5m"`
  - Apex rests a limit order **at the 9 EMA** and fills only when a bar's wick retests it.
  - `ema_retest_max_overshoot_pct: 0.2` (wick may break 9 EMA by ≤0.2%)
  - `ema_retest_timeout_minutes: 30` (pending signal expires if no retest in 30 min)
- **Slope gate:** `min_5m_slope_pct: 0.15` — the proven V3.1 value. (Rich said "1.5";
  read as 0.15, the EMA9 3-bar slope gate. A literal 1.5% would almost never fire.)
- **ATR body-band filter (ON):** `block_body_atr_band: [0.3, 0.5]` — skip entries where
  the signal candle body is 0.3–0.5× ATR (mid-body chop). Carried from V3.1/Reclaim.
- **Sunday block:** OFF. Apex trades all 7 days.

---

## 4. Sizing

- **Margin:** $250 fixed per entry (not compounding).
- **Leverage:** 30x → $7,500 notional per position.
- **Account:** $3,000.
- **Margin mode:** isolated.

---

## 5. Exit Ladder (3 stages)

The original V3.1 bridge had a 4-state ladder (breakeven → lock-profit → trail-jump →
trail). Apex collapses this to the exact 3 stages Rich described:

| Stage | Trigger | Action |
|---|---|---|
| 0 — Initial | on fill | Hard SL at **−$30** (`sl_loss_usdt = 30`) |
| 1 — Breakeven | P&L ≥ **+$20** | SL → entry price (`breakeven_usdt = 20`) |
| 2 — Trail | P&L ≥ **+$35** | SL jumps to **+$20 locked**, then trails **$15 behind** price on each new high (`trail_activate_usdt = 35`, `trail_distance_usdt = 15`) |

**Why $20 is "locked" at +$35:** trailing $15 behind a +$35 peak = +$20. So the lock and
the trail are the same mechanism — at activation the SL sits at +$20; as price makes new
highs the SL trails $15 behind, never dropping below the +$20 floor.

**Implementation note:** drop the intermediate `lock_profit` state (state 2 in the old
machine) and the dead-zone (state 3). Apex states: `0=initial → 1=breakeven → 2=trailing`.
On entering state 2, place SL at `entry ± (trail_activate − trail_distance)` = +$20, then
update on each new high to `price − trail_distance`. Long and short symmetric.

All thresholds are P&L in USDT, converted to a price distance per-position via
`notional = margin × leverage; price_dist = (usdt / notional) × ref_price`.

---

## 6. Venue: Lighter

The original V3.1 executed on BloFin (`blofin_client`). Apex swaps the execution layer
for the **Lighter client** already proven in the Reclaim/Scalper/Rebound bridges. Only the
"place / cancel order, read price" layer changes; the EMA9 retest, SL ladder, and sizing
logic stay identical. Lighter is zero-fee, which is why these tight-margin EMA-retest
setups are viable here and not on BloFin.

Open implementation detail (resolve in plan): Lighter fills are maker/taker different from
BloFin's attached-SL model. The resting-limit-at-9EMA entry and the cancel/replace SL
trail must be expressed in Lighter's order primitives (same approach the other Lighter
bridges already use).

**EMA9 retest data source:** the EMA9 (and slope / body-ATR) used for the entry gate are
recomputed locally from **Lighter's** OHLCV for each coin, so the retest level matches the
instrument actually being filled — not TV's feed.

---

## 7. Coins

Launch basket: **HYPE, SOL, ZEC.** Same exit/sizing config applies to all three at the
baseline (per-coin overrides possible later, not in this build).

---

## 8. Components

| Component | Responsibility |
|---|---|
| `webhook` | Receive SMRT Pro V3 alert, validate secret, enqueue pending signal |
| `signal_engine` / `signals` | Recompute EMA9, slope, body/ATR locally on closed bars |
| `entry_gate` | Per-symbol kill switch (pause/resume entries) |
| `poller` | Drive pending-signal lifecycle (rest/refresh/fill EMA9 limit) + run the 3-stage SL ladder on open positions |
| Lighter client | Place/cancel orders, fetch price, fetch instrument (copied from proven bridge) |
| `state` / `db` | Persist positions, SL order ids, trade log → `apex.db` |
| `notify` | `@apexbot` — entry + exit messages only (silent on trail steps) |
| `telegram_control` | `@apexbot` inbound — `/off` `/on` `/close` `/status` + kill-all (pause/stop trading) |
| cooldown breaker | 3 consecutive losing closes → block all entries 60 min, auto-resume |
| `config` | `config.apex.yaml` — all knobs above |

---

## 9. Acceptance Criteria

1. `scripts/apex/` runs standalone; `grep` shows **zero** imports from `scripts/scalping/`
   or other bridge packages.
2. A TV alert to `/webhook/apex` enqueues a pending signal; a simulated 9 EMA wick retest
   fills the entry; no fill occurs without a retest within the timeout.
3. Slope gate (0.15) and ATR band [0.3, 0.5] both block as configured (unit-tested).
4. SL ladder unit tests: −$30 initial; SL→entry at +$20; at +$35 SL→+$20 then trails $15
   behind each new high; symmetric for long and short.
5. Sizing: $250 × 30x = $7,500 notional, isolated margin, on HYPE/SOL/ZEC.
6. Sunday entries are NOT blocked.
7. Orders route to Lighter, not BloFin.
8. 3 consecutive losing trail closes block all new entries for 60 min, then
   auto-resume (unit-tested: trail close feeds `_register_close`).
9. `@apexbot` `/off` / `/on` / `/close` / `/status` / kill-all pause and resume
   trading; bot token read from `TELEGRAM_BOT_TOKEN` env, absent from all
   committed files.

---

## 10. Deferred / Future (not this build)

- New Apex dashboard UI (own isolated dir already reserved).
- Slope-gate / ATR-band tuning, per-coin overrides.
- Possible narrowing to one coin after live observation.
- Any daily-target / session-stop behavior, if Rich wants it later.
