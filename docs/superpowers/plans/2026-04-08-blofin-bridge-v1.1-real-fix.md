# BloFin Bridge v1.1 — Server-Side TP/SL via ATR

**Goal:** Replace alert-driven TP/SL with real BloFin stop + limit orders placed at entry time, computed from ATR. After this, only Buy/Sell alerts are needed; TP/SL fills happen server-side on BloFin.

**Architecture:** At entry, bridge fetches recent OHLCV via ccxt, computes Wilder's ATR(N), derives SL distance (`sl_atr_multiplier × ATR`) and TP prices (`tp_atr_multipliers[i] × ATR` from entry), places market entry with attached SL + 3 reduce-only limit orders. A background polling loop watches for TP fills and advances the SL to breakeven / TP1 price (P2 step-stop).

**Tech Stack:** unchanged from v1.0. Python 3.11 · FastAPI · uvicorn · ccxt · pydantic-settings · sqlite3 · httpx · pytest.

**Config additions:**
```yaml
defaults:
  atr_length: 14
  sl_atr_multiplier: 3.0
  tp_atr_multipliers: [1.0, 2.0, 3.0]
  tp_split: [0.40, 0.30, 0.30]
  atr_timeframe: "5m"
  poll_interval_seconds: 10
```

**SL and TPs are independently tunable** — `sl_atr_multiplier` and `tp_atr_multipliers` share the same underlying ATR calculation but drive separate distances. Tighten SL without touching TPs or vice versa.

---

## Task Breakdown

### Task 1: ATR module (pure math, TDD)
`src/blofin_bridge/atr.py` — Wilder's ATR implementation. Input: list of `[ts, o, h, l, c, v]`, output: float. Test fixture: hand-computed ATR(3) from known values.

### Task 2: BloFinClient.fetch_recent_ohlcv
Add method wrapping `ccxt.fetch_ohlcv`. Returns last N candles. Unit test with mocked ccxt.

### Task 3: Config schema extension
Add `atr_length`, `sl_atr_multiplier`, `tp_atr_multipliers`, `atr_timeframe`, `poll_interval_seconds` to Defaults. Keep `safety_sl_pct` as a fallback for cases where ATR fetch fails. Update tests.

### Task 4: SQLite schema migration + state.py methods
Add columns: `tp1_order_id TEXT`, `tp2_order_id TEXT`, `tp3_order_id TEXT`, `sl_distance REAL`, `atr_value REAL`. Add `record_tp_order_ids(pid, tp1, tp2, tp3)` and `record_tp_filled(pid, stage)` methods. Wipe `bridge.db` on deploy (simpler than ALTER). Tests.

### Task 5: BloFinClient.place_limit_reduce_only
Add method for placing reduce-only limit orders at a specific price. Used for TP1/TP2/TP3. Tests with mocked ccxt.

### Task 6: Rewrite handle_entry
New flow:
1. Reject if position already open (unchanged)
2. Fetch recent OHLCV via `blofin.fetch_recent_ohlcv`
3. Compute ATR
4. Compute `sl_distance = atr × sl_atr_multiplier`
5. Compute SL price and TP1/TP2/TP3 prices (side-aware)
6. Place market entry with attached SL at the real SL price (not 5% safety)
7. Place 3 reduce-only limit orders at TP1/TP2/TP3 prices with 40/30/30 sizes
8. Store all 4 order ids + sl_distance + atr_value in SQLite
9. Return result with all computed levels

Full TDD with mocked BloFin. Existing entry tests need updating.

### Task 7: Position poller (background asyncio task)
New module `src/blofin_bridge/poller.py`:
- `async def poll_positions(store, blofin, interval)` — runs in a loop
- Every `interval` seconds: fetch BloFin positions, compare each bridge-tracked open position's `current_size` to BloFin's reported size
- If BloFin size is less than SQLite size, a TP filled. Determine which stage by how much size dropped (40% → TP1, 70% → TP1+TP2, 100% → all TPs).
- On TP1 fill: cancel current SL, place new SL at entry price (breakeven). Update SQLite `tp_stage=1`.
- On TP2 fill: cancel SL, place new SL at TP1 price. Update `tp_stage=2`.
- On TP3 fill (position flat): cancel SL, archive position.
- Wire into FastAPI lifespan via `asyncio.create_task` in `main.create_app`.
- Tests with mocked BloFin and time control.

### Task 8: Simplify legacy handlers
- `handlers/tp.py` — keep as a backup force-close path (if a TP alert ever arrives and the position still has that stage open, honor it). Clean up the now-redundant SL placement code inside.
- `handlers/sl.py` — keep unchanged (backup force-close).
- `router.py` — no changes (all 8 actions still routed).
- Rationale: the poller is the primary path, alerts become belt-and-braces. User can delete TP/SL alerts in TradingView but the bridge gracefully handles either setup.

### Task 9: Full test suite + VPS redeploy
Run `pytest -v`, verify all green. Scp everything, wipe bridge.db on VPS, rebuild container, verify /health, verify instruments loaded.

### Task 10: Live probe
Run a synthetic `buy` curl against the deployed bridge, observe BloFin demo UI for the 4 new orders (1 stop + 3 limits), confirm bridge `/status` shows them. Fire `sl` to close. Confirm clean shutdown.

---

## Acceptance

- [ ] 59 existing tests still pass + new tests for ATR, poller, schema
- [ ] `docker logs blofin-bridge` shows "poll_positions started" at startup
- [ ] A single `buy` webhook produces 4 new orders on BloFin demo (visible in Open Orders + Position panel)
- [ ] SL and TP prices match Pro V3's painted levels within ~5% (close enough — the formula is "ATR + 13 EMA" variant, standard ATR is slightly different)
- [ ] SL order is tunable independently of TP orders via config
- [ ] Demo trade lifecycle works end-to-end (buy → TP1 fills naturally → SL moves to breakeven → ...)
