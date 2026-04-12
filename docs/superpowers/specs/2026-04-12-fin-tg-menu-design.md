# Fin Telegram Control Menu — Design

**Status:** Approved
**Date:** 2026-04-12
**Project:** `scripts/scalping/` (Fin, the BloFin scalping bridge)

## Problem

Fin currently posts one-way Telegram notifications (entry, trail, close).
There is no way to pause new entries without SSH-ing into the VPS and
editing config or stopping the container. During choppy markets this
forced the operator to wait out bad entries they could see coming.

## Goal

Give the operator a per-symbol kill switch reachable from their phone:
stop new entries on a symbol with a tap, resume with a tap, see current
state at any time. In-flight open positions and their trail/SL logic must
be unaffected so an existing trade runs to its natural conclusion.

## Non-goals

- No flattening of open positions from Telegram (too destructive for v1).
- No changes to entry logic, SL math, trail behavior, sizing.
- No persistence of paused state across container restarts. If the
  container restarts, all symbols default to running. Operator re-pauses
  if desired.
- No remote config editing, no new symbol onboarding via TG.

## Scope

Per-symbol pause for the two symbols currently supported (`SOL-USDT`,
`ZEC-USDT`) plus an `all` alias. The design must not assume a fixed
symbol set — the gate reads the active symbol list from the config so
adding a third symbol later requires no commander changes.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ scalping container                                      │
│                                                         │
│  ┌─────────────┐     ┌──────────────────┐               │
│  │ FastAPI     │     │ PositionPoller   │               │
│  │ /webhook... │     │ (existing)       │               │
│  └──────┬──────┘     └────────┬─────────┘               │
│         │                     │                         │
│         ▼                     ▼                         │
│  ┌──────────────────────────────────────┐               │
│  │  EntryGate (new, in-memory)          │               │
│  │  paused: set[str]                    │◄──┐           │
│  └──────────────────────────────────────┘   │           │
│                                              │           │
│  ┌──────────────────────────────────────┐   │           │
│  │ TelegramCommander (new, bg task)     │───┘           │
│  │ - getUpdates long poll (25s)         │               │
│  │ - handles /menu /stop /start /status │               │
│  │ - inline keyboard callbacks          │               │
│  │ - auth: allowed_user_id only         │               │
│  └──────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────┘
```

## Components

### `EntryGate` (new, `src/blofin_bridge/entry_gate.py`)

Small singleton wrapping a `set[str]` of paused symbols. Thread-safe via
`asyncio.Lock` around mutations (commander runs in the same event loop
as the poller and the FastAPI handlers, so a simple lock is enough).

```python
class EntryGate:
    def __init__(self, symbols: Iterable[str]) -> None: ...
    def is_paused(self, symbol: str) -> bool: ...
    async def pause(self, symbol: str) -> None: ...
    async def resume(self, symbol: str) -> None: ...
    async def pause_all(self) -> None: ...
    async def resume_all(self) -> None: ...
    def status(self) -> dict[str, bool]:  # {"SOL-USDT": True, ...}
```

No DB writes — state lives only in the process.

### `TelegramCommander` (new, `src/blofin_bridge/tg_commander.py`)

Async background task launched from the same FastAPI `lifespan` context
as `PositionPoller`. Calls Telegram `getUpdates` with `timeout=25` (long
poll), dispatches commands, and replies via `sendMessage` /
`editMessageText`.

Responsibilities:
- Auth check: reject any update whose `from.id` does not match the
  configured `allowed_user_id`. Silent reject (no reply) so the bot's
  existence is not leaked.
- Command dispatch for `/menu`, `/status`, `/stop <sym>`, `/start <sym>`.
- Inline keyboard callback handler (uses the same command functions
  internally so behavior is identical to typing the slash command).
- Keyboard renderer that reflects live `EntryGate.status()` — buttons
  change label/icon to show current state.
- On every action, the menu message is re-edited so the operator always
  sees the authoritative current state.
- Collaborates with `Store` to cancel any pending EMA-retest signals for
  a paused symbol.

Interface Fin exposes through existing `Notifier.send` is unchanged; the
commander uses its own HTTP client for bidirectional work.

### `Store.cancel_pending_signals(symbol: str) -> int` (edit)

New method. Marks any `pending_signals` row with `status='pending'` and
matching symbol as `status='cancelled'` with `filled_at=now`. Returns the
count cancelled (for the commander's reply text: "SOL paused, 1 pending
signal cancelled").

### Router / webhook handler (edit)

Before creating a pending signal from an incoming TV alert, the handler
calls `gate.is_paused(symbol)`. If paused, it returns HTTP 200 with body
`{"status":"paused","symbol":"SOL-USDT"}` and does not touch the DB. TV
gets a clean 200 (so its alert does not go into an error-retry state)
and the commander logs the block.

### PositionPoller (edit)

In `_process_pending_signals`, before calling `handle_entry`, the poller
checks `gate.is_paused(sig["symbol"])`. If paused, the pending signal is
expired with reason `paused` and skipped. This covers the race where a
signal was queued, then the operator paused the symbol before the EMA
retest fired.

### `main.py` lifespan (edit)

Constructs `EntryGate` and `TelegramCommander`, starts the commander in
`lifespan.startup`, cancels it in `lifespan.shutdown`. The existing
`PositionPoller` start/stop pattern is followed exactly.

### `config.py` (edit)

Adds `telegram.allowed_user_id: int` to the settings model, loaded from
the `TG_ALLOWED_USER_ID` env var. Defaults to the known operator ID
(`6421609315`) so an ops mistake doesn't leave the bot wide open.

## Commands & UX

| Command | Effect |
|---|---|
| `/menu` | Post a message with the inline keyboard below. |
| `/status` | Text reply summarizing paused/running state + any open positions. |
| `/stop <sym>` | Pause entries for `sol` / `zec` / `all`. Cancels any pending signal for that symbol. Idempotent. |
| `/start <sym>` | Resume entries for `sol` / `zec` / `all`. Idempotent. |

Inline keyboard posted by `/menu`:

```
┌──────────────┬──────────────┐
│ ⏸ Stop SOL   │ ⏸ Stop ZEC   │
├──────────────┼──────────────┤
│ ▶ Start SOL  │ ▶ Start ZEC  │
├──────────────┴──────────────┤
│     📊 Status               │
└─────────────────────────────┘
```

When a symbol is paused, its Stop button flips to a disabled-looking
label (`⏸ SOL paused`) and its Start button becomes active. The message
body above the keyboard shows the current status line, e.g.:

```
Fin — control
SOL-USDT: ⏸ paused
ZEC-USDT: ▶ running
```

Every button tap triggers `editMessageText` with the new body + keyboard
so stale state is impossible to see.

## Data flow

### Normal entry (no pause)

1. TV alert → webhook handler → `gate.is_paused("SOL-USDT")` → `False`.
2. Handler creates a pending signal in SQLite as today.
3. Poller sees pending signal, checks gate again, EMA retest hits, entry
   fires. Unchanged from current behavior.

### Pause during an incoming alert

1. Operator taps `⏸ Stop SOL`.
2. Commander calls `gate.pause("SOL-USDT")` and
   `store.cancel_pending_signals("SOL-USDT")`.
3. Commander edits the menu message to show SOL paused + reply to the
   action button indicating "SOL paused, N pending signals cancelled".
4. Next TV alert for SOL arrives → webhook handler sees gate paused,
   returns 200 `{"status":"paused"}`, no DB row created, no notification
   (the Notifier stays quiet on paused-block to avoid alert spam).

### Resume

1. Operator taps `▶ Start SOL`.
2. Commander calls `gate.resume("SOL-USDT")`.
3. Commander edits the menu to show SOL running. No retroactive signal
   replay — paused alerts are lost by design; Fin starts trading on the
   next new alert.

## Error handling

- **`getUpdates` network error** — log warning, sleep 5s, retry. Loop
  never dies. Consecutive failures log at error level after N attempts
  but still recover.
- **Telegram 429 rate limit** — honor `retry_after` from the response,
  capped at 60s.
- **Unauthorized sender** — update is dropped silently. No reply, no
  telemetry leak.
- **Unknown command** — polite one-line reply listing the valid
  commands, only if from the authorized user.
- **`/stop sol` when already paused** — reply "SOL already paused",
  return 0 cancelled signals. Idempotent.
- **Button tap with stale state** — commander recomputes state from
  `gate.status()` on every callback; stale client state cannot produce
  an inconsistent DB write.
- **Container restart while paused** — by design, paused state is lost.
  Operator is expected to re-pause on the next TV alert or re-open
  `/menu` on boot. Documented in the commit message and deploy notes.
- **Commander crash** — lifespan task wrapper logs the traceback and
  re-raises so the container exits and Docker restarts it. This is
  loud on purpose: a dead commander means no kill switch, and silent
  degradation is worse than a crash-loop alert.

## Testing

### Unit

- `tests/test_entry_gate.py` — new. Pause/resume/is_paused/status,
  pause_all/resume_all, unknown symbol raises `ValueError`.
- `tests/test_tg_commander.py` — new. Mock `httpx.AsyncClient`, feed
  fake `getUpdates` payloads, assert:
  - `/stop sol` pauses SOL only.
  - `/stop all` pauses everything.
  - `/start sol` resumes SOL.
  - Unauthorized sender produces zero outbound calls.
  - Button callback dispatches identically to text command.
  - Keyboard renderer reflects `EntryGate.status()`.
  - Rate-limit response path honors `retry_after`.
- `tests/test_state.py` — edit. New test for
  `cancel_pending_signals` cancelling only matching symbol, returning
  correct count.

### Integration

- `tests/test_router.py` — edit. Webhook for a paused symbol returns
  `{"status":"paused"}`, no pending signal row created, no position
  opened.
- `tests/test_poller.py` — edit. Pending signal for a paused symbol is
  expired (not executed) by the poller on the next cycle. Existing open
  position for the same symbol continues to be polled for trail/SL.

## Deployment

1. scp changed `src/` files to `/docker/scalping/src/blofin_bridge/`.
2. `docker compose up -d --build` on VPS.
3. Smoke test from Telegram:
   - `/menu` → keyboard renders, both symbols show running.
   - `⏸ Stop SOL` → status flips, no pending.
   - Fire a dummy TV alert or wait for a real one → verify webhook
     returns paused and no entry is opened.
   - `▶ Start SOL` → next alert trades as normal.
4. Update `.env` on VPS if `TG_ALLOWED_USER_ID` is not already set
   (default value in code already matches the operator).

## Risks

- **Bot token reuse.** The existing `TELEGRAM_BOT_TOKEN` is the same one
  sending notifications. Adding `getUpdates` on the same token is fine
  (Telegram allows it), but only one listener at a time may call
  `getUpdates`. If another instance (dev machine, old container) is
  still polling, updates get split randomly. Deployment checklist: make
  sure no stale instance is running.
- **Long poll vs shutdown.** A 25s long poll blocks graceful shutdown
  for up to 25s. Acceptable. The lifespan task is cancelled cleanly.
- **Silent pause on alert storm.** If SOL is paused during a storm, the
  operator gets no TG notifications about blocked alerts (by design to
  prevent spam). `/status` always shows the truth.

## Rollback

`docker compose down && git checkout <prev commit> && docker compose up -d --build`
on the VPS restores the previous behavior. No DB migrations involved —
the only schema impact is the `pending_signals.status='cancelled'` value,
which is a new string value in an existing TEXT column and is
backward-compatible.
