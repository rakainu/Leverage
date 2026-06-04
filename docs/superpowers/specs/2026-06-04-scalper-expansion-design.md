# Scalper expansion — design (2026-06-04)

Three **independent** workstreams on the live `regime_mr` paper bridge
(`scalper-bridge`, `/docker/scalper-paper`). Approved by Rich 2026-06-04.

Context: the 4-coin basket went −$419 / 73% WR through a market crash (06-04 alone
−$596). We are NOT abandoning the edge — we're (1) widening the test set to learn
which coins to keep when conditions smooth out, (2) building a per-ticker entry
kill-switch (paper now, live-ready), and (3) a TradingView visualizer.

---

## WS1 — More test tickers + sizing

**Goal:** gather per-coin edge data across a wider, liquid basket so we know which
coins to keep. Per-coin PF/WR is the metric (sizing-invariant per the sizing sweep).

**Tickers (10-coin basket).** Current 4 + 6 most-liquid Lighter crypto perps:

| Symbol | market_id | 24h vol |
|---|---|---|
| ETH | 0 | $558M |
| BTC | 1 | $1.68B (re-enabled — WS bug that disabled it is gone; now REST snapshot) |
| SOL | 2 | $71M |
| XRP | 7 | $3.5M |
| WLD | 6 | $14M |
| NEAR | 10 | $14M |
| TON | 12 | $8M |
| HYPE | 24 | $218M |
| BNB | 25 | $4.2M |
| ZEC | 90 | $42M |

**Sizing (Rich's call): 10x · $500 margin · $6,000 start.**
- Leverage **10x** — validated sweet spot: 0 liquidations (clears the 7.3% max
  adverse move), best net/DD (15.1), full PF. Right call coming out of a crash.
- **$500 margin × 10x = $5,000 notional/position**, uniform across all 10 coins
  (apples-to-apples per-coin comparison).
- **$6,000 starting capital** — covers all 10 open at once ($500 × 10 = $5,000)
  plus ~20% buffer so the 10th entry is never blocked by unrealized swings.
- Net $ scales linearly with notional; size is just a dial. The point of this
  phase is per-coin PF/WR, not $ returns.

**Changes:**
- `config.scalper.yaml`: 10 symbols, each `enabled: true, margin_usdt: 500,
  leverage: 10`; `initial_collateral_usdc: 6000`.
- Dashboard `config.scalper.yaml` (`/docker/lighter-dashboard`):
  `initial_collateral_usdc: 6000` to MATCH (known gotcha — mismatch = wrong
  equity + phantom DD on the UI).
- DB reset to a clean $6,000 baseline (archive the current `scalper.db` to
  `.bak-20260604/`). Equity-curve continuity is intentionally reset — we're
  starting the wide-basket experiment.

**VPS load:** 10 markets × REST snapshot/3s ≈ 3–4% CPU (currently 1.4% at 4).
Trivial; well within headroom alongside live `scalping-v3.1`.

---

## WS2 — Per-ticker entry switch (Telegram, live-ready)

**Behavior:**
- `/off SYM` → block **new** entries on SYM. Any OPEN position keeps being managed
  to its natural SL/TP/time exit. Any unfilled resting maker-limit is cancelled
  (an unfilled limit is a not-yet-open new entry). OFF is never destructive.
- `/on SYM` → re-enable new entries.
- `/close SYM` → force-close an open position on SYM now (market), reason
  `manual`. Separate explicit command so OFF never closes by accident.
- `/status` → list each symbol's on/off state + open positions + flat/pending.

**Coded properly for live (exchange-agnostic):**
- Switch state is a first-class per-symbol flag, **persisted in SQLite**
  (`ticker_switch` table) so it survives restarts. Unknown symbol defaults to ON.
- Gated at the single entry-decision point via `Bridge._entries_allowed(symbol)`,
  checked in EVERY open path (regime signal-arm, regime pending fill, the
  retest/webhook fire paths). The same gate carries verbatim to a live executor.
- Telegram control runs as its own asyncio task (`telegram_control.py`):
  long-poll `getUpdates` with offset tracking, **authorized to Rich's chat ID
  only** (`TELEGRAM_CHAT_ID` env = 6421609315), resilient to network errors
  (catch + backoff + resume; never crashes the bridge). Replies confirm each
  action. Reuses the existing Scalper bot token.

**Files:** `db.py` (table + `get_switches`/`set_switch`), new
`telegram_control.py`, `config.py` (a `control:` block — `telegram_enabled`),
`main.py` (load switches at startup, `_entries_allowed`, `force_close`, gate the
entry paths, start the control task). Unit tests for switch persistence, the
gating predicate, and command parse/authorization.

---

## WS3 — TradingView strategy (visualize entries/SL)

**Goal:** see what the strategy is doing on the chart. Pine **v6 `strategy()`**,
standalone, **zero scalper bridge code touched**. Not running TV's backtester now;
it's for live signal visualization.

**Replicates `regime.py` exactly:**
- EMA(200) trend gate; slope = EMA − EMA[20]; sign sets regime.
- Session-anchored VWAP (daily reset).
- z-score(30) of (close − VWAP); fade at |z| ≥ 1.5 **only** with the trend
  (long dips in uptrend / short rips in downtrend).
- Maker-limit entry at close ∓ 0.25·ATR(14); SL = 2.0·ATR; TP = 0.3 × |VWAP −
  limit|; time stop 12 bars.
- Plots: EMA200, VWAP, z bands; entry/SL/TP markers via `strategy.entry`/`exit`.

**File:** `pinescripts/scalper_regime_mr.pine`. Build with the **pinescript-v6**
skill; verify it compiles via **tradingview-connect** (needs TV Desktop open).

---

## Sequencing
1. WS2 code (TDD) + WS1 config — deploy together (one restart), reset DB, verify.
2. WS3 Pine — independent; build + compile-check.
Each workstream is independently revertable.
