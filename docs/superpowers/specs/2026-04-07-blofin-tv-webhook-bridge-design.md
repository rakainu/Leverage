# BloFin × TradingView Webhook Bridge — Design

**Date:** 2026-04-07
**Owner:** Rich (@rakainu)
**Status:** Draft — awaiting user review before implementation plan

## 1. Goal

Automate SMRT Algo Pro V3 signals from TradingView into BloFin perpetual futures trades, with programmable take-profit laddering and step-stop SL management, running on the existing Hostinger VPS.

In scope for v1:
- One symbol: `SOL-USDT` (perpetual)
- Pro V3 alert events: Buy, Sell, TP1, TP2, TP3, SL, Reversal Buy, Reversal Sell
- Fixed-margin position sizing ($100 USDT default, configurable)
- Fixed leverage (10x default, configurable)
- TP ladder: 40% / 30% / 30% split (configurable)
- SL policy P2 — step-stop: hard 5% safety SL at entry → move to entry on TP1 → move to TP1 price on TP2 → flat at TP3 (pluggable interface, P2 is v1 default)
- BloFin demo environment first, live graduation after paper validation

Out of scope for v1 (noted so we don't accidentally design around them):
- Multi-symbol (architecture supports it; we only configure SOL-USDT)
- BOS / CHOCH structural alerts
- Trailing stop policies P3/P4 (interface exists; not wired)
- Pine Script strategy conversion (we drive a closed-source indicator)
- Backtesting / historical replay

## 2. Why this shape

Three constraints forced the design:

**(i) Pro V3 is an indicator, not a strategy.** TradingView's `{{strategy.*}}` placeholders return empty strings, which rules out BloFin's native Signal Bot webhook — it requires `strategy.market_position` / `strategy.prev_market_position` to infer intent. We cannot produce those from an indicator alert. The only viable path is a bridge that tracks position state itself and calls BloFin's REST API directly.

**(ii) BloFin has no native trailing stop order type.** Confirmed by grepping the full API docs and ccxt's BloFin adapter (`'trailing': False`). Any trailing or step-stop behavior must be synthesized client-side by cancel-and-replacing the TP/SL order. There is also no amend endpoint — only cancel + re-post.

**(iii) Pro V3 TP alerts don't carry direction.** The `TP1`/`TP2`/`TP3` conditions fire regardless of whether the original entry was Buy or Sell. The bridge's own position state is the only honest source of truth for which side a TP belongs to. This makes stateful position tracking mandatory, not optional.

These three facts force the same conclusion: **a stateful FastAPI bridge on our infrastructure, driving BloFin REST via ccxt, with SQLite for position state.**

## 3. Architecture

```
┌─────────────────────┐
│   TradingView       │
│   Pro V3 alerts     │
│   (one per event)   │
└──────────┬──────────┘
           │ HTTPS POST (JSON)
           │ { "secret": "...", "action": "buy",
           │   "symbol": "SOLUSDT.P" }
           ▼
┌─────────────────────────────────────────────────┐
│  FastAPI bridge  (VPS, behind Traefik/Caddy)    │
│                                                  │
│  ┌──────────────┐  ┌──────────────────────┐     │
│  │ /webhook     │→ │  Signal Router       │     │
│  │ (auth + log) │  │  (dispatch by action)│     │
│  └──────────────┘  └──────────┬───────────┘     │
│                               │                  │
│  ┌────────────────────────────▼───────────────┐ │
│  │  Position Manager                          │ │
│  │  - reads/writes SQLite state               │ │
│  │  - applies SL policy (P2 step-stop v1)     │ │
│  └────────────────────────────┬───────────────┘ │
│                               │                  │
│  ┌────────────────────────────▼───────────────┐ │
│  │  BloFin Client (ccxt + raw fallback)       │ │
│  │  - set_leverage, place_order (+attached SL)│ │
│  │  - order-tpsl (partial close, SL move)     │ │
│  │  - close-position (full exit, reversal)    │ │
│  └────────────────────────────┬───────────────┘ │
│                               │                  │
│  ┌──────────────┐  ┌──────────▼───────────┐     │
│  │ SQLite       │  │ Telegram notifier    │     │
│  │ (bridge.db)  │  │ (trade events)       │     │
│  └──────────────┘  └──────────────────────┘     │
└──────────┬──────────────────────────────────────┘
           │ HTTPS (signed requests)
           ▼
    BloFin REST API
    openapi.blofin.com  (live)
    demo-trading-openapi.blofin.com  (paper)
```

### Why each piece exists

| Component | Reason |
|---|---|
| **FastAPI** | TV webhook needs a public HTTPS endpoint; FastAPI has fast JSON, pydantic validation, and plays well with Python's ccxt. |
| **SQLite** | One process, small dataset, crash-safe. Overkill alternatives (Postgres, Redis) add deploy friction for zero benefit. |
| **ccxt (primary) + `blofin` PyPI (fallback)** | ccxt handles BloFin's awkward hex-string-to-bytes signing quirk for us. For any BloFin-specific call ccxt doesn't cover, we fall through to the community `blofin` package (Nomeida) or raw `requests`. |
| **Traefik/Caddy** | Already the TLS gateway on the VPS per `reference_vps_layout.md`. Bridge slots in with a new domain or path — reuses existing certs. |
| **Telegram notifier** | You already DM with the main OpenClaw agent; piggyback on existing Telegram tooling for trade alerts with `FROM: BLOFIN_BRIDGE` sender tag. |
| **Demo env first** | BloFin exposes `demo-trading-openapi.blofin.com` with mintable demo funds. Same code, env var flip. De-risk signing and flow logic before real money. |

## 4. Data flow — the eight events

The bridge handles exactly eight incoming alert actions. Each one is a short, deterministic state transition:

### 4.1 `buy` (open long)
1. Verify no existing open position on SOL-USDT. If one exists, log and reject (Pro V3 shouldn't fire Buy while a trade is already open; this is a safety check, not expected flow).
2. Compute `size` in contracts:
   `contracts = round_to_lot( (margin_usdt * leverage) / (price * contract_value) )`
   where `price` = last mark price, `contract_value` and `lot_size` come from the cached instrument info.
3. `POST /api/v1/trade/order` with:
   - `side=buy`, `orderType=market`, `positionSide=net`, `marginMode=cross`
   - Attached SL: `slTriggerPrice = entry_est * (1 - safety_pct)`, `slOrderPrice = -1` (market execution)
4. On fill, write position row to SQLite: `{symbol, side=long, entry_price, size, safety_sl_order_id, tp_policy=P2, tp_stage=0, opened_at}`.
5. Telegram: `FROM: BLOFIN_BRIDGE\nOPEN LONG SOL @ $X — size Y contracts, SL $Z (safety)`.

### 4.2 `sell` (open short)
Mirror of 4.1 with `side=sell`, SL above entry at `entry_est * (1 + safety_pct)`.

### 4.3 `tp1` (partial take profit 1)
1. Load current position. If flat, log and discard (stale alert).
2. Close `tp1_pct` of position (40% default): `POST /api/v1/trade/order` as a reduce-only market order with `side` flipped from the entry side and `size = floor(initial_size * tp1_pct)`. Record the fill price as `tp1_fill_price`.
3. Cancel the existing safety SL (`POST /api/v1/trade/cancel-tpsl` with the stored `sl_order_id`).
4. Place a new standalone SL via `POST /api/v1/trade/order-tpsl`:
   - Long position: `slTriggerPrice = entry_price`, `slOrderPrice = -1`, `side = sell`, `size = -1` (entire remaining position), `reduceOnly = "true"`.
   - Short position: `slTriggerPrice = entry_price`, `slOrderPrice = -1`, `side = buy`, `size = -1`, `reduceOnly = "true"`.
   - This is break-even protection on the remaining 60%.
5. Update SQLite: `tp_stage=1`, `current_size -= closed`, new `sl_order_id`, `tp1_fill_price`.
6. Telegram: `TP1 HIT — closed 40%, SL moved to breakeven ($X)`.

### 4.4 `tp2` (partial take profit 2)
1. Load position, verify `tp_stage=1`. If not, log inconsistency (we somehow missed TP1).
2. Close `tp2_pct` of *original* position size (30%) — so ~50% of remaining.
3. Cancel existing SL, place new SL at the **fill price of TP1** (stored in SQLite at TP1 time). This locks ≥ TP1 profit on the rest.
4. Update SQLite: `tp_stage=2`, new SL order id, remaining size.
5. Telegram: `TP2 HIT — closed 30%, SL locked at TP1 price ($X)`.

### 4.5 `tp3` (final take profit)
1. Load position, verify `tp_stage=2`.
2. Close remaining size (30%) via reduce-only market.
3. Cancel outstanding SL order.
4. Delete or archive position row.
5. Telegram: `TP3 HIT — position closed. Total PnL: $X`.

### 4.6 `sl` (Pro V3 stop loss fired)
Pro V3's own SL signal. Treat as full exit regardless of BloFin's SL order:
1. Load position.
2. Call `POST /api/v1/trade/close-position` (BloFin's full-close endpoint).
3. Cancel any outstanding TP/SL algo orders for the symbol.
4. Delete position row.
5. Telegram: `PRO V3 SL HIT — forced close. PnL: $X`.

### 4.7 `reversal_buy` / `reversal_sell`
Single-message flip. If a position in the opposite direction exists:
1. Close current position (`close-position`).
2. Cancel all outstanding algo orders for the symbol.
3. Immediately proceed to the `buy` (or `sell`) handler to open the new side.
4. Telegram two messages: `CLOSED SHORT — PnL $X. OPENING LONG @ $Y`.

If no opposite position exists, behave like a normal `buy` / `sell`.

### 4.8 Rate-limit safety

Worst-case burst during a reversal: `close-position` + `cancel-algo` (x2) + `place-order` (with attached SL) = 4 requests. Well under the 30-req/10s trading limit. No special throttling needed for v1.

## 5. TradingView alert configuration

One alert per Pro V3 condition per symbol. Each alert uses the same webhook URL and a fixed JSON body. No TradingView placeholders needed — the action is hard-coded in the message.

**Webhook URL:** `https://<bridge-host>/webhook/pro-v3`

**Message body examples:**

```json
// Buy alert
{"secret":"<SHARED_SECRET>","symbol":"SOL-USDT","action":"buy","source":"pro_v3"}

// TP1 alert
{"secret":"<SHARED_SECRET>","symbol":"SOL-USDT","action":"tp1","source":"pro_v3"}

// SL alert
{"secret":"<SHARED_SECRET>","symbol":"SOL-USDT","action":"sl","source":"pro_v3"}
```

The `source` field is forward-looking: when we add other indicators, they tag their own alerts so the bridge can route per-source policies.

**Trigger setting:** "Once Per Bar Close" for entries and TPs (matches Pro V3's paint logic, avoids intrabar flicker).

**Eight alerts to create per symbol:** `buy`, `sell`, `tp1`, `tp2`, `tp3`, `sl`, `reversal_buy`, `reversal_sell`.

## 6. Configuration

All tunables live in `config/blofin_bridge.yaml` (gitignored) and are hot-reloaded at startup. A minimal config:

```yaml
env: demo          # demo | live
bloFin:
  api_key: "${BLOFIN_API_KEY}"
  api_secret: "${BLOFIN_API_SECRET}"
  passphrase: "${BLOFIN_PASSPHRASE}"

bridge:
  shared_secret: "${BRIDGE_SECRET}"
  bind_host: "0.0.0.0"
  bind_port: 8787
  telegram_chat_id: "${TELEGRAM_CHAT_ID}"
  telegram_bot_token: "${TELEGRAM_BOT_TOKEN}"

defaults:
  margin_usdt: 100
  leverage: 10
  safety_sl_pct: 0.05      # 5% hard SL at entry
  tp_split: [0.40, 0.30, 0.30]
  sl_policy: p2_step_stop  # p2_step_stop | p1_breakeven | p3_trail | p4_hybrid

symbols:
  SOL-USDT:
    enabled: true
    margin_usdt: 100       # per-symbol overrides
    leverage: 10
    sl_policy: p2_step_stop
```

## 7. Code structure

```
scripts/blofin-bridge/
├── main.py                 # FastAPI app, webhook routes
├── config.py               # pydantic-settings, YAML + env loader
├── state.py                # SQLite schema + DAO
├── router.py               # action dispatch → handlers
├── handlers/
│   ├── entry.py            # buy, sell
│   ├── tp.py               # tp1, tp2, tp3
│   ├── sl.py               # sl (Pro V3 exit)
│   └── reversal.py         # reversal_buy, reversal_sell
├── policies/
│   ├── base.py             # SLPolicy interface
│   ├── p1_breakeven.py
│   ├── p2_step_stop.py     # v1 default
│   ├── p3_trail.py         # stub for future
│   └── p4_hybrid.py        # stub for future
├── blofin_client.py        # ccxt wrapper + raw fallback
├── sizing.py               # margin+leverage+price → contracts
├── notify.py               # Telegram
├── db/
│   └── schema.sql
├── tests/
│   ├── test_sizing.py
│   ├── test_policies.py
│   ├── test_handlers.py    # mocked BloFin client
│   └── test_webhook_e2e.py # FastAPI TestClient
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## 8. SL policy interface

The pluggable piece that lets us experiment without rewriting handlers:

```python
class SLPolicy(Protocol):
    def on_entry(self, position: Position) -> SLOrder: ...
    def on_tp(self, position: Position, tp_stage: int, tp_fill_price: float) -> SLOrder | None: ...
    def on_tick(self, position: Position, last_price: float) -> SLOrder | None: ...  # unused by P2
```

`P2StepStop` implementation is ~30 lines: breakeven on stage 1, TP1 price on stage 2, noop on tick. `P3Trail` and `P4Hybrid` are stubs that live alongside it so swapping is a config-only change.

## 9. Database schema

```sql
CREATE TABLE positions (
    id              INTEGER PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL CHECK (side IN ('long','short')),
    entry_price     REAL NOT NULL,
    initial_size    REAL NOT NULL,  -- contracts
    current_size    REAL NOT NULL,  -- decreases at each TP
    tp_stage        INTEGER NOT NULL DEFAULT 0,  -- 0 / 1 / 2 / 3
    tp1_fill_price  REAL,
    tp2_fill_price  REAL,
    sl_order_id     TEXT,           -- current active SL order on BloFin
    sl_policy       TEXT NOT NULL,
    opened_at       DATETIME NOT NULL,
    closed_at       DATETIME,
    realized_pnl    REAL,
    source          TEXT             -- 'pro_v3' etc.
);

CREATE INDEX idx_positions_symbol_open ON positions (symbol) WHERE closed_at IS NULL;

CREATE TABLE events (
    id          INTEGER PRIMARY KEY,
    position_id INTEGER REFERENCES positions(id),
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,      -- raw webhook JSON
    received_at DATETIME NOT NULL,
    handled_at  DATETIME,
    outcome     TEXT,               -- ok | error | skipped
    error_msg   TEXT
);
```

## 10. Error handling

| Failure mode | Handling |
|---|---|
| BloFin rejects order (insufficient margin, min size, etc.) | Log event with outcome=error, Telegram alert, do NOT retry automatically — user investigates. |
| BloFin 429 rate limit | Exponential backoff (250ms → 1s → 4s), max 3 retries. |
| BloFin signature invalid | Hard failure, Telegram alert — likely config/clock drift issue. |
| Webhook auth fails (wrong secret) | HTTP 401, log, no Telegram (avoid spam from probes). |
| Unknown action value | HTTP 400, log, no trade action. |
| TP alert arrives with no open position (stale) | Log as "skipped", no trade action, no alert. |
| TP alert arrives for wrong stage (e.g., TP2 without TP1) | Log inconsistency, proceed with best-effort (close tp2_pct from current, reset stage tracking). |
| Bridge crash mid-trade | On restart, reconcile: fetch BloFin positions and open orders, cross-check SQLite. Any drift → Telegram alert, freeze the symbol until manual ack. |

The reconciliation step on restart is the most important reliability feature. It handles power loss, deploys, and crashes cleanly.

## 11. Observability

- **Logs:** JSON lines to stdout, captured by Docker. Each event carries a request id.
- **Telegram:** trade open/close, SL moves, any error.
- **Health endpoint:** `GET /health` → `{status, bloFin_reachable, symbols_enabled, open_positions}`.
- **Status endpoint (auth-gated):** `GET /status?secret=...` → detailed view of current positions, SL order ids, last N events.

## 12. Deployment

- **Location:** `/docker/blofin-bridge/` on the Hostinger VPS, following the same pattern as `openclaw-wmo9`.
- **Compose service:** one container, ports bound locally, Traefik router exposes `blofin-bridge.<your-domain>` over HTTPS.
- **Secrets:** `/docker/blofin-bridge/.env` holds BloFin API keys, bridge shared secret, Telegram token. Never committed.
- **Backup:** nightly dump of `bridge.db` included in the existing VPS backup routine.

## 13. Testing plan

**Unit tests** (fast, in CI):
- Sizing math: margin × leverage ÷ price → contracts for varied instruments.
- Policy transitions: P2 on_entry / on_tp for each stage, with mocked positions.
- Handler dispatch: webhook router picks correct handler for each action.
- Error paths: unknown action, wrong secret, stale TP.

**Integration tests** (mocked BloFin):
- Full happy path: buy → tp1 → tp2 → tp3, assert ccxt calls in order.
- Reversal path: long open → reversal_sell → verify close + open.
- Crash-recovery: start bridge with inconsistent SQLite vs mocked positions, expect Telegram alert.

**Demo-env end-to-end:**
- Point bridge at `demo-trading-openapi.blofin.com`, mint demo funds, fire a synthetic curl webhook for each action, watch position lifecycle on BloFin demo UI.

**Live smoke test:**
- Switch env to live, $10-USDT margin override, single SOL-USDT trade, manual TV alert fired once. Verify full cycle. Raise margin only after two successful cycles.

## 14. Open questions

Nothing blocking. A few for after v1:
- Should we record mark price at each webhook arrival for slippage analysis?
- Should we expose a per-symbol kill switch (disable new entries, let open positions run)?
- How do we version the SL policy so historical trades can be analyzed against the policy that was active at the time?

## 15. Decisions log

| Decision | Choice | Why |
|---|---|---|
| Architecture | Self-hosted FastAPI bridge → BloFin REST | Pro V3 is indicator, not strategy; BloFin Signal Bot incompatible. |
| Stack | Python + FastAPI + ccxt + SQLite | Fits existing VPS tooling; ccxt handles BloFin signing quirk. |
| Symbol (v1) | SOL-USDT | User's current focus; chart already on it. |
| Margin | $100 USDT fixed (configurable) | Stable USD risk per trade. |
| Leverage | 10x (configurable) | User preference. |
| TP split | 40/30/30 (configurable) | Banks faster, leaves runner. |
| SL policy | P2 step-stop (pluggable) | Deterministic profit lock; no trail math required. |
| Safety SL | 5% hard stop, attached at entry | Protects if bridge crashes. |
| Trailing stop | Interface exists, not wired in v1 | Deferred until P2 performance is understood. |
| Environment | Demo → live graduation | Validates signing + flow without real risk. |
| Auth | Shared secret in JSON body | Simple, sufficient for closed endpoint behind HTTPS. |
