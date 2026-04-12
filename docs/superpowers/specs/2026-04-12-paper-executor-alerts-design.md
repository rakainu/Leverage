# Paper Executor + Alerts + Outcome Tracking (Plan 3) — Design Spec

**Status:** Approved
**Author:** Claude (with Rich)
**Date:** 2026-04-12
**Parent spec:** `docs/superpowers/specs/2026-04-11-meme-runner-design.md`
**Depends on:** Plan 2c (scoring engine) complete

---

## 1. Purpose

Add paper trade execution, rich Telegram alerts, and milestone-based outcome tracking to the runner intelligence pipeline. The goal is to **evaluate signal quality** — whether the scoring engine selects good candidates — not to simulate trading strategy execution.

Paper positions record entry price at signal time, then snapshot performance at fixed intervals (5m, 30m, 1h, 4h, 24h) with max favorable/adverse excursion tracking. No trailing stops, no active position management. Clean analytical data.

## 2. Scope

**In scope:**
- `PaperExecutor` — consumes scored candidates, opens paper positions for strong_candidate and probable_runner
- `MilestoneSnapshotter` — background loop that captures price milestones and closes positions at 24h
- `TelegramAlerter` — sends HTML entry and close alerts with score breakdown
- `formatting.py` — pure formatting helpers (top 3 reasons, cautions, HTML templates)
- `paper_positions` table with outcome tracking columns
- main.py wiring to replace `_drain_scored`

**Out of scope:**
- Live trading (no Jupiter swaps)
- Trailing stop / active position management (future phase)
- Dashboard display of positions (future)
- Milestone alerts to Telegram (only entry + 24h close)

## 3. Architecture decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Position monitoring | Milestone snapshots, NOT trailing stop | Goal is evaluating signal quality, not strategy execution. Trailing stops add noise. |
| Alert frequency | Entry + 24h close only | Milestone updates too noisy for Telegram. Milestones stored in DB for dashboard later. |
| Formatting split | `formatting.py` separate from `telegram.py` | Formatting is pure/testable. Telegram.py handles I/O only. |
| Alert payloads | Structured dicts on alert_bus, not preformatted HTML | Keeps formatting centralized, easier to change later. |
| Dedup | `UNIQUE(runner_score_id)` on paper_positions | One score → one paper position. Schema-enforced. |
| Entry price | Fetched from DexScreener at executor handle time | Explicit anchor. No position without a price. |

## 4. Data flow

```
scored_bus (ScoredCandidate)
    |
    v
PaperExecutor.run()
    |-- verdict not in (strong_candidate, probable_runner)? → skip, log reason
    |-- enable_executor is False? → skip, log reason
    |-- price fetch fails? → skip, log warning
    |-- duplicate runner_score_id? → skip, log
    |-- otherwise:
    |       → fetch entry price from DexScreener
    |       → determine position size from weights.yaml
    |       → INSERT paper_positions (status=open)
    |       → build structured entry alert dict
    |       → alert_bus.put(entry_alert)
    |
    v
MilestoneSnapshotter.run()  — background loop every 30s
    |-- SELECT all paper_positions WHERE status = 'open'
    |-- for each open position:
    |       → guard: skip if entry_price_sol <= 0
    |       → fetch current price
    |       → if price fetch fails: log, retry next cycle
    |       → compute current P&L %
    |       → UPDATE max_favorable_pct, max_adverse_pct (every cycle)
    |       → check milestones (5m, 30m, 1h, 4h, 24h):
    |           → if elapsed >= threshold AND column IS NULL:
    |               → write price + pnl (first-write-only SQL)
    |       → if 24h milestone just written:
    |           → close position (status=closed, close_reason=completed)
    |           → build structured close alert dict
    |           → alert_bus.put(close_alert)
    |       → if open > 36h and 24h still NULL (persistent failures):
    |           → close with close_reason=error
    |           → persist reason in notes_json
    |           → no Telegram alert for error closures
    |
    v
alert_bus (structured dict)
    |
    v
TelegramAlerter.run()
    |-- route by alert["type"]: "runner_entry" or "runner_close"
    |-- call formatting helpers to produce HTML
    |-- send via python-telegram-bot
    |-- if bot_token empty: drain silently, log once at startup
```

## 5. Paper positions schema

```sql
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_mint TEXT NOT NULL,
    symbol TEXT,
    runner_score_id INTEGER NOT NULL REFERENCES runner_scores(id),
    verdict TEXT NOT NULL,
    runner_score REAL NOT NULL,
    entry_price_sol REAL NOT NULL,
    entry_price_usd REAL,
    amount_sol REAL NOT NULL,
    signal_time TIMESTAMP NOT NULL,
    entry_source TEXT NOT NULL DEFAULT 'paper_executor_v1',

    -- Milestone snapshots (first-write-only, NULL until captured)
    price_5m_sol REAL, pnl_5m_pct REAL,
    price_30m_sol REAL, pnl_30m_pct REAL,
    price_1h_sol REAL, pnl_1h_pct REAL,
    price_4h_sol REAL, pnl_4h_pct REAL,
    price_24h_sol REAL, pnl_24h_pct REAL,

    -- Excursion (updated continuously while open)
    max_favorable_pct REAL DEFAULT 0.0,
    max_adverse_pct REAL DEFAULT 0.0,

    -- Lifecycle
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    close_reason TEXT CHECK (close_reason IN ('completed', 'error')),
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    notes_json TEXT,

    -- Dedup
    UNIQUE(runner_score_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_mint ON paper_positions(token_mint);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_paper_positions_verdict ON paper_positions(verdict);
```

**Semantics:**
- `signal_time` — the `scored_at` timestamp from ScoredCandidate, distinct from `opened_at` (DB insert time)
- `entry_price_sol` — fetched from DexScreener at PaperExecutor handle time. This is the anchor for all P&L.
- `max_favorable_pct` — highest P&L % seen during 24h window (>= 0)
- `max_adverse_pct` — lowest P&L % seen during 24h window (<= 0, negative means drawdown)
- `notes_json` — lightweight extensibility. Stores `entry_price_source` (e.g. `"dexscreener"`), error closure reasons, etc.
- Milestone columns: NULL until captured, never overwritten after first write.

**Migration:** Same PRAGMA table_info pattern — fresh DBs get the table from schema, existing DBs get CREATE TABLE IF NOT EXISTS.

## 6. PaperExecutor — `executor/paper.py`

```python
class PaperExecutor:
    def __init__(
        self,
        scored_bus: asyncio.Queue,
        alert_bus: asyncio.Queue,
        weights: WeightsLoader,
        price_fetcher: PriceLiquidityFetcher,
        db: Database,
        enable_executor: bool = True,
    ):
```

### `run()` — long-lived async consumer

1. Read `ScoredCandidate` from `scored_bus`
2. Check eligibility:
   - `verdict` not in (`strong_candidate`, `probable_runner`) → skip, log `"skip_verdict"`
   - `enable_executor` is False → skip, log `"executor_disabled"`
3. Fetch current price: `price_fetcher.fetch(mint)` → `{"price_sol": ..., "price_usd": ...}`
   - If fails → skip, log `"skip_price_fetch_failed"`
4. Determine position size from weights.yaml:
   - `probable_runner` → `position_sizing.probable_runner_sol`
   - `strong_candidate` → `position_sizing.strong_candidate_sol`
5. INSERT into `paper_positions`:
   - `runner_score_id = sc.filtered.enriched.cluster_signal_id` — wait, this is wrong. Use the `runner_scores.id` from the scoring persistence step. Since `ScoredCandidate` doesn't carry the DB row ID, we need to look it up or thread it. Options:
     - **Option A:** Query `runner_scores` by `token_mint + created_at` to find the row ID
     - **Option B:** Add `runner_score_db_id: int | None` to `ScoredCandidate`, populated by `ScoringEngine._persist()`
   - **Decision: Option B** — thread the ID. Same pattern as `cluster_signal_id`. Small upstream change: `ScoringEngine._persist()` reads `lastrowid`, uses `dataclasses.replace()` on the frozen ScoredCandidate, then emits the copy with the ID set.
   - If UNIQUE constraint fails → skip, log `"skip_duplicate_score_id"`
6. Build entry alert dict:
   ```python
   {
       "type": "runner_entry",
       "paper_position_id": position_id,
       "runner_score_id": score_db_id,
       "token_mint": mint,
       "symbol": symbol,
       "verdict": verdict,
       "runner_score": score,
       "amount_sol": amount,
       "entry_price_sol": price_sol,
       "entry_price_usd": price_usd,
       "cluster_summary": {
           "wallet_count": N,
           "tier_counts": {"A": 2, "B": 1},
           "convergence_minutes": 14.0,
       },
       "explanation": sc.explanation,  # full scoring breakdown
   }
   ```
7. Put on `alert_bus`
8. Log entry

### Entry price source

Stored in `notes_json` as `{"entry_price_source": "dexscreener"}` for traceability.

## 7. MilestoneSnapshotter — `executor/snapshotter.py`

```python
class MilestoneSnapshotter:
    def __init__(
        self,
        alert_bus: asyncio.Queue,
        price_fetcher: PriceLiquidityFetcher,
        db: Database,
        check_interval_sec: float = 30.0,
    ):
```

### `run()` — background loop

```python
async def run(self):
    while True:
        await asyncio.sleep(self.check_interval_sec)
        await self._check_all()
```

### `_check_all()`

```python
async def _check_all(self):
    rows = await self._get_open_positions()
    for pos in rows:
        await self._check_one(pos)
```

### `_check_one(pos)`

1. Guard: if `entry_price_sol <= 0` → log error, skip
2. Compute elapsed seconds: `now - signal_time`
3. Fetch current price via `price_fetcher.fetch(mint)`
4. If price fetch fails → log, return (retry next cycle)
5. Compute current P&L %: `(current_price - entry_price) / entry_price * 100`
6. Update MFE/MAE (every cycle while open):
   ```sql
   UPDATE paper_positions
   SET max_favorable_pct = MAX(max_favorable_pct, ?),
       max_adverse_pct = MIN(max_adverse_pct, ?)
   WHERE id = ?
   ```
7. Check milestones in order:
   ```python
   MILESTONES = [
       (5 * 60,    "5m",  "price_5m_sol",  "pnl_5m_pct"),
       (30 * 60,   "30m", "price_30m_sol", "pnl_30m_pct"),
       (60 * 60,   "1h",  "price_1h_sol",  "pnl_1h_pct"),
       (4 * 3600,  "4h",  "price_4h_sol",  "pnl_4h_pct"),
       (24 * 3600, "24h", "price_24h_sol", "pnl_24h_pct"),
   ]
   ```
   For each: if `elapsed >= threshold` AND column IS NULL → first-write-only UPDATE:
   ```sql
   UPDATE paper_positions
   SET {price_col} = ?, {pnl_col} = ?
   WHERE id = ? AND {price_col} IS NULL
   ```
8. If 24h milestone just written:
   - Write milestone first
   - Then close: `status='closed'`, `close_reason='completed'`, `closed_at=now`
   - Build close alert dict and put on `alert_bus`
9. Error closure: if open > 36h and 24h milestone still NULL:
   - Close with `close_reason='error'`
   - Persist `{"error_closure_reason": "persistent_price_failures"}` in `notes_json`
   - No Telegram alert

### Close alert dict

```python
{
    "type": "runner_close",
    "paper_position_id": position_id,
    "runner_score_id": score_id,
    "token_mint": mint,
    "symbol": symbol,
    "verdict": verdict,
    "runner_score": score,
    "entry_price_sol": entry,
    "entry_price_usd": entry_usd,
    "exit_price_sol": current_price,
    "milestones": {
        "5m": pnl_5m_pct,    # or None if not captured
        "30m": pnl_30m_pct,
        "1h": pnl_1h_pct,
        "4h": pnl_4h_pct,
        "24h": pnl_24h_pct,
    },
    "max_favorable_pct": mfe,
    "max_adverse_pct": mae,
}
```

## 8. TelegramAlerter — `alerts/telegram.py`

```python
class TelegramAlerter:
    def __init__(
        self,
        alert_bus: asyncio.Queue,
        bot_token: str,
        chat_id: str,
    ):
```

### `run()` — long-lived consumer

1. If `bot_token` is empty → log once at startup: `"telegram_disabled"`, then drain queue silently
2. Read alert dict from `alert_bus`
3. Route by `alert["type"]`:
   - `"runner_entry"` → call `format_entry_alert(alert)` from `formatting.py`
   - `"runner_close"` → call `format_close_alert(alert)` from `formatting.py`
4. Send via `telegram.Bot(token).send_message(chat_id, html, parse_mode="HTML")`
5. If send fails → log warning, do not retry (fire and forget)

## 9. Alert formatting — `alerts/formatting.py`

Pure functions, no I/O. Fully testable.

### `format_entry_alert(alert: dict) -> str`

Produces HTML:

```html
<b>FROM: RUNNER • STRONG CANDIDATE (72)</b>

<b>$WIFHAT</b> • <code>5HpY...abc1</code>
Cluster: 4 wallets (2A, 1B, 1U) in 14 min

<b>Why it scored well:</b>
  1. Wallet Quality    87 (x0.20 = 17.3)
  2. Rug/Risk          77 (x0.15 = 11.6)
  3. Entry Quality     75 (x0.15 = 11.3)

<b>Cautions:</b>
  Holder Quality 38 — below threshold

Paper entry: 0.25 SOL @ $0.00042

<a href="https://dexscreener.com/solana/{mint}">DexScreener</a> | <a href="https://solscan.io/token/{mint}">Solscan</a>
```

### `format_close_alert(alert: dict) -> str`

Produces HTML:

```html
<b>FROM: RUNNER • CLOSED • $WIFHAT (72 → STRONG CANDIDATE)</b>

Final P&L: +18.3%
Entry: $0.00042 → Exit: $0.00050

Milestones:
  5m:  +8.1%
  30m: +22.4%
  1h:  +45.2%
  4h:  +31.0%
  24h: +18.3%

MFE: +52.1% | MAE: -3.2%
```

Only shows milestones that were captured (skip any that are None).

### `format_top_reasons(explanation: dict) -> list[tuple[str, float, float, float]]`

1. Extract `explanation["dimensions"]`
2. Sort by `weighted` descending
3. Exclude dimensions where `detail.get("placeholder") is True` (narrative in v1)
4. Return top 3 as `[(name, score, weight, weighted), ...]`

### `format_cautions(explanation: dict) -> list[str]`

1. Any dimension with `score < 40` → `"{name} {score} — below threshold"`
2. `explanation["data_degraded"]` is True → `"Data degraded — missing {', '.join(missing_subscores)}"`
3. Rug risk dimension `detail.get("insider_capped")` is True → `"Insider risk cap triggered"`
4. If empty → `["No major cautions"]`

### `mint_short(mint: str) -> str`

`mint[:4]...{mint[-4:]}` — e.g. `5HpY...abc1`

### `escape_html(text: str) -> str`

Escape `<`, `>`, `&` in any user/token-derived text. Applied to symbol, caution text, etc.

### Symbol truncation

`symbol[:12]` if longer than 12 chars.

## 10. Upstream change: thread `runner_score_db_id` into ScoredCandidate

Small change to `ScoringEngine.run()` loop:

1. In `_persist()`, after INSERT, read `cursor.lastrowid` and return it
2. In `run()`, after `_persist()` returns the DB ID, use `dataclasses.replace(scored, runner_score_db_id=db_id)` to produce the copy
3. Emit the copy (with ID) onto `scored_bus` instead of the original

Add field to `ScoredCandidate`:

```python
runner_score_db_id: int | None = None
```

Same pattern as `ClusterSignal.id` threading in Plan 2c Task 1.

## 11. weights.yaml additions

New keys under `executor`:

```yaml
executor:
  check_interval_sec: 30
  error_closure_hours: 36
```

Existing `position_sizing` keys already present — no changes needed.

## 12. main.py wiring

Replace `_drain_scored` with three supervised tasks:

```python
alert_bus: asyncio.Queue = asyncio.Queue()

paper_executor = PaperExecutor(
    scored_bus=scored_bus,
    alert_bus=alert_bus,
    weights=weights,
    price_fetcher=price_fetcher,
    db=db,
    enable_executor=settings.enable_executor,
)

snapshotter = MilestoneSnapshotter(
    alert_bus=alert_bus,
    price_fetcher=price_fetcher,
    db=db,
    check_interval_sec=float(weights.get("executor.check_interval_sec", 30)),
)

telegram = TelegramAlerter(
    alert_bus=alert_bus,
    bot_token=settings.telegram_bot_token,
    chat_id=settings.telegram_chat_id,
)

# In asyncio.gather — replace _drain_scored with:
_supervise(paper_executor.run, "paper_executor", logger),
_supervise(snapshotter.run, "milestone_snapshotter", logger),
_supervise(telegram.run, "telegram_alerter", logger),
```

Delete `_drain_scored` function.

Reuse existing `price_fetcher` from enrichment layer (already constructed in main.py).

## 13. File layout

```
meme-trading/runner/
  executor/
    __init__.py
    paper.py              # PaperExecutor
    snapshotter.py        # MilestoneSnapshotter
  alerts/
    __init__.py
    telegram.py           # TelegramAlerter (I/O only)
    formatting.py         # Pure formatting helpers
  tests/
    unit/
      test_paper_executor.py
      test_snapshotter.py
      test_alert_formatting.py
      test_telegram_alerter.py
    integration/
      test_scoring_to_executor.py
```

## 14. Test strategy

### Unit tests — `test_paper_executor.py`

| Test | What it verifies |
|---|---|
| Opens position for strong_candidate | INSERT succeeds, correct amount_sol from config |
| Opens position for probable_runner | Correct larger amount_sol |
| Skips ignore verdict | No INSERT, log "skip_verdict" |
| Skips watch verdict | No INSERT, log "skip_verdict" |
| Skips when executor disabled | No INSERT, log "executor_disabled" |
| Skips on price fetch failure | No INSERT, log "skip_price_fetch_failed" |
| Skips duplicate runner_score_id | UNIQUE constraint handled gracefully |
| Emits entry alert after INSERT | Alert on alert_bus with correct type and fields |
| Does not alert if INSERT fails | Alert bus stays empty |

### Unit tests — `test_snapshotter.py`

| Test | What it verifies |
|---|---|
| Captures 5m milestone | price_5m_sol and pnl_5m_pct written |
| First-write-only | Second call does not overwrite existing milestone |
| Updates MFE/MAE every cycle | Values track running extremes |
| MAE is negative for drawdowns | Correct sign convention |
| Closes at 24h | status=closed, close_reason=completed |
| Close alert emitted at 24h | Alert on alert_bus with milestones |
| Skips on price fetch failure | Position stays open, no milestone written |
| Error closure at 36h | close_reason=error, notes_json has reason |
| Skips corrupted entry_price | entry_price_sol <= 0 skipped safely |

### Unit tests — `test_alert_formatting.py`

| Test | What it verifies |
|---|---|
| format_top_reasons excludes narrative | Placeholder dimension not in top 3 |
| format_top_reasons sorts by weighted | Correct ordering |
| format_cautions with low dimension | Dimension < 40 shown |
| format_cautions with data_degraded | Missing data mentioned |
| format_cautions with insider cap | Insider cap mentioned |
| format_cautions with none | Returns "No major cautions" |
| format_entry_alert HTML structure | Valid HTML, escaped symbols |
| format_close_alert skips missing milestones | Only captured milestones shown |
| mint_short format | 4...4 format correct |
| escape_html handles special chars | `<>&` properly escaped |

### Unit tests — `test_telegram_alerter.py`

| Test | What it verifies |
|---|---|
| Routes runner_entry to format_entry | Correct formatter called |
| Routes runner_close to format_close | Correct formatter called |
| Drains silently when no bot_token | Queue consumed, no send |
| Handles send failure gracefully | Logs warning, continues |

### Integration test — `test_scoring_to_executor.py`

End-to-end: ScoredCandidate → PaperExecutor → verify paper_positions row + alert_bus payload.

---

**End of spec.**
