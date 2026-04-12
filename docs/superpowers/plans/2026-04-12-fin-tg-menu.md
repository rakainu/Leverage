# Fin Telegram Control Menu — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram inline-menu control surface to Fin (the BloFin scalping bridge) that lets the operator pause or resume new entries per symbol from their phone without SSH.

**Architecture:** A new in-memory `EntryGate` singleton tracks paused symbols. The existing `router.dispatch` and `PositionPoller._process_pending_signals` consult the gate before creating or firing a trade. A new `TelegramCommander` async background task long-polls Telegram `getUpdates`, dispatches slash commands + inline-button callbacks, and edits a persistent menu message to reflect live state.

**Tech Stack:** Python 3.12, FastAPI, httpx (async), pydantic-settings, SQLite (existing), asyncio, pytest/pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-12-fin-tg-menu-design.md`

---

## File Structure

**New files**
- `scripts/scalping/src/blofin_bridge/entry_gate.py` — `EntryGate` class (paused-symbol set + async-safe mutators).
- `scripts/scalping/src/blofin_bridge/tg_commander.py` — `TelegramCommander` class (getUpdates loop, auth, dispatch, keyboard rendering, HTTP helpers).
- `scripts/scalping/tests/test_entry_gate.py` — unit tests for the gate.
- `scripts/scalping/tests/test_tg_commander.py` — unit tests for commander command dispatch, auth, keyboard rendering.

**Modified files**
- `scripts/scalping/src/blofin_bridge/router.py` — `dispatch` takes a new `gate` param and returns a paused response instead of queueing a pending signal.
- `scripts/scalping/src/blofin_bridge/poller.py` — `PositionPoller` takes a new `gate` param and skips/expires any pending signal for a paused symbol before EMA-retest evaluation.
- `scripts/scalping/src/blofin_bridge/config.py` — add `telegram_allowed_user_id: int` to `BridgeCreds` (env var `TG_ALLOWED_USER_ID`, default `6421609315`).
- `scripts/scalping/src/blofin_bridge/main.py` — construct `EntryGate` + `TelegramCommander`, pass gate into `dispatch` and `PositionPoller`, start/stop commander in `lifespan`.
- `scripts/scalping/tests/test_poller.py` — add a "pending signal for paused symbol is expired" test.
- `scripts/scalping/tests/test_router.py` — add a "dispatch returns paused response when gate.is_paused" test.

**Out of scope**
- Any changes to SL/trail/sizing/entry math.
- Any persistence of paused state.
- Any Traefik config or new public endpoints.

---

## Conventions

- Symbols are full instrument IDs: `SOL-USDT`, `ZEC-USDT`. Aliases accepted on the TG side: `sol`, `zec`, `all` (case-insensitive). The commander normalizes aliases before calling the gate.
- Running directory for tests: `C:\Users\rakai\Leverage\scripts\scalping`. Run `python -m pytest` from there.
- After each task commits, run `python -m pytest -q` to confirm the whole suite is green (currently 104 tests).

---

## Task 1: `EntryGate` class + unit tests

**Files:**
- Create: `scripts/scalping/src/blofin_bridge/entry_gate.py`
- Create: `scripts/scalping/tests/test_entry_gate.py`

- [ ] **Step 1: Write the failing tests**

Create `scripts/scalping/tests/test_entry_gate.py`:

```python
import asyncio

import pytest

from blofin_bridge.entry_gate import EntryGate


@pytest.fixture
def gate():
    return EntryGate(symbols=["SOL-USDT", "ZEC-USDT"])


def test_defaults_all_running(gate):
    assert gate.is_paused("SOL-USDT") is False
    assert gate.is_paused("ZEC-USDT") is False
    assert gate.status() == {"SOL-USDT": False, "ZEC-USDT": False}


@pytest.mark.asyncio
async def test_pause_and_resume(gate):
    await gate.pause("SOL-USDT")
    assert gate.is_paused("SOL-USDT") is True
    assert gate.is_paused("ZEC-USDT") is False
    assert gate.status() == {"SOL-USDT": True, "ZEC-USDT": False}

    await gate.resume("SOL-USDT")
    assert gate.is_paused("SOL-USDT") is False


@pytest.mark.asyncio
async def test_pause_all_and_resume_all(gate):
    await gate.pause_all()
    assert gate.status() == {"SOL-USDT": True, "ZEC-USDT": True}

    await gate.resume_all()
    assert gate.status() == {"SOL-USDT": False, "ZEC-USDT": False}


@pytest.mark.asyncio
async def test_pause_unknown_symbol_raises(gate):
    with pytest.raises(ValueError):
        await gate.pause("DOGE-USDT")


@pytest.mark.asyncio
async def test_pause_idempotent(gate):
    await gate.pause("SOL-USDT")
    await gate.pause("SOL-USDT")  # second call should not error
    assert gate.is_paused("SOL-USDT") is True


def test_is_paused_unknown_symbol_returns_false(gate):
    # A symbol we don't know about cannot be paused, so treat as running.
    assert gate.is_paused("DOGE-USDT") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:\Users\rakai\Leverage\scripts\scalping && python -m pytest tests/test_entry_gate.py -v`
Expected: all tests fail with `ModuleNotFoundError: No module named 'blofin_bridge.entry_gate'`.

- [ ] **Step 3: Write minimal implementation**

Create `scripts/scalping/src/blofin_bridge/entry_gate.py`:

```python
"""In-memory per-symbol kill switch for new entries.

Consulted by `router.dispatch` before queueing a pending signal from a
TV webhook, and by `PositionPoller._process_pending_signals` before
firing an EMA-retest entry. State lives only in the process — a
container restart resets all symbols to running.
"""
from __future__ import annotations
import asyncio
from typing import Iterable


class EntryGate:
    """Tracks which symbols are currently blocked from opening new trades.

    Methods that mutate state are async and serialize on an asyncio.Lock
    so that concurrent commander + poller calls cannot interleave writes
    to the underlying set.
    """

    def __init__(self, symbols: Iterable[str]) -> None:
        self._known: set[str] = set(symbols)
        self._paused: set[str] = set()
        self._lock = asyncio.Lock()

    def is_paused(self, symbol: str) -> bool:
        return symbol in self._paused

    def status(self) -> dict[str, bool]:
        return {sym: (sym in self._paused) for sym in sorted(self._known)}

    async def pause(self, symbol: str) -> None:
        if symbol not in self._known:
            raise ValueError(f"unknown symbol {symbol}")
        async with self._lock:
            self._paused.add(symbol)

    async def resume(self, symbol: str) -> None:
        if symbol not in self._known:
            raise ValueError(f"unknown symbol {symbol}")
        async with self._lock:
            self._paused.discard(symbol)

    async def pause_all(self) -> None:
        async with self._lock:
            self._paused = set(self._known)

    async def resume_all(self) -> None:
        async with self._lock:
            self._paused.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_entry_gate.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/entry_gate.py scripts/scalping/tests/test_entry_gate.py
git commit -m "scalping: EntryGate — in-memory per-symbol entry kill switch"
```

---

## Task 2: Wire `EntryGate` into `router.dispatch`

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/router.py`
- Modify: `scripts/scalping/tests/test_router.py`

- [ ] **Step 1: Write the failing test**

Add this test to `scripts/scalping/tests/test_router.py` (append, don't replace existing tests):

```python
import asyncio

from blofin_bridge.entry_gate import EntryGate
from blofin_bridge.router import dispatch


def test_dispatch_returns_paused_when_gate_is_paused(store_fixture, blofin_mock):
    """When the EntryGate has the symbol paused, dispatch must NOT create
    a pending signal and must return a paused response."""
    gate = EntryGate(symbols=["SOL-USDT", "ZEC-USDT"])
    asyncio.get_event_loop().run_until_complete(gate.pause("SOL-USDT"))

    symbol_configs = {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 30,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "ema_retest_timeout_minutes": 30,
        },
    }

    result = dispatch(
        action="buy", symbol="SOL-USDT",
        store=store_fixture, blofin=blofin_mock,
        symbol_configs=symbol_configs, gate=gate,
    )

    assert result == {
        "paused": True,
        "symbol": "SOL-USDT",
        "action": "buy",
        "reason": "entries paused by operator",
    }
    # No pending signal row should have been created.
    assert store_fixture.list_pending_signals() == []


def test_dispatch_sl_not_blocked_by_gate(store_fixture, blofin_mock):
    """SL close actions are never blocked — operator safety."""
    gate = EntryGate(symbols=["SOL-USDT"])
    asyncio.get_event_loop().run_until_complete(gate.pause("SOL-USDT"))

    symbol_configs = {
        "SOL-USDT": {
            "enabled": True, "margin_usdt": 100, "leverage": 30,
            "margin_mode": "isolated", "sl_policy": "p2_step_stop",
            "ema_retest_timeout_minutes": 30,
        },
    }

    result = dispatch(
        action="sl", symbol="SOL-USDT",
        store=store_fixture, blofin=blofin_mock,
        symbol_configs=symbol_configs, gate=gate,
    )
    # SL action goes through handle_sl — result has "closed" or "no_position"
    assert "paused" not in result
```

If `store_fixture` / `blofin_mock` fixtures don't exist yet in `test_router.py`, add at the top of the file:

```python
import pytest
from unittest.mock import MagicMock

from blofin_bridge.state import Store


@pytest.fixture
def store_fixture(tmp_path):
    return Store(tmp_path / "router.db")


@pytest.fixture
def blofin_mock():
    m = MagicMock()
    m.fetch_last_price.return_value = 300.0
    return m
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_router.py::test_dispatch_returns_paused_when_gate_is_paused -v`
Expected: fail with `TypeError: dispatch() got an unexpected keyword argument 'gate'`.

- [ ] **Step 3: Add `gate` parameter to `dispatch`**

Edit `scripts/scalping/src/blofin_bridge/router.py`. Replace the `dispatch` function signature and early logic with:

```python
def dispatch(
    *,
    action: str,
    symbol: str,
    store: Store,
    blofin: BloFinClient,
    symbol_configs: dict[str, dict[str, Any]],
    gate: "EntryGate | None" = None,
) -> dict[str, Any]:
    if action not in VALID_ACTIONS:
        raise UnknownAction(action)

    sym_cfg = symbol_configs.get(symbol)
    if sym_cfg is None:
        return {"opened": False, "handled": False,
                "reason": f"unknown symbol {symbol}"}
    if not sym_cfg.get("enabled", False):
        return {"opened": False, "handled": False,
                "reason": f"symbol {symbol} disabled in config"}

    # Operator-initiated pause: block new entries but allow SL (always close-safe).
    entry_actions = ("buy", "sell", "reversal_buy", "reversal_sell")
    if gate is not None and action in entry_actions and gate.is_paused(symbol):
        return {
            "paused": True,
            "symbol": symbol,
            "action": action,
            "reason": "entries paused by operator",
        }

    if action in ("buy", "sell"):
        ...  # unchanged
```

Also add the forward-reference import at the top of `router.py`:

```python
from __future__ import annotations
from typing import Any, TYPE_CHECKING

from .blofin_client import BloFinClient
from .handlers.entry import handle_entry
from .handlers.reversal import handle_reversal
from .handlers.sl import handle_sl
from .state import Store

if TYPE_CHECKING:
    from .entry_gate import EntryGate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_router.py -v`
Expected: all existing router tests still pass, plus the two new ones.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/router.py scripts/scalping/tests/test_router.py
git commit -m "scalping: router.dispatch honors EntryGate pause for entry actions"
```

---

## Task 3: Wire `EntryGate` into `PositionPoller`

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/poller.py`
- Modify: `scripts/scalping/tests/test_poller.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/scalping/tests/test_poller.py`:

```python
from blofin_bridge.entry_gate import EntryGate


@pytest.mark.asyncio
async def test_pending_signal_for_paused_symbol_is_expired(store, blofin):
    """Signal queued before pause → poller expires it instead of firing."""
    # Seed a pending signal
    sig_id = store.create_pending_signal(
        symbol="SOL-USDT", action="buy", signal_price=300.0,
        timeout_minutes=30,
    )

    gate = EntryGate(symbols=["SOL-USDT"])
    await gate.pause("SOL-USDT")

    poller = _make_poller(store, blofin, gate=gate)
    await poller.poll_once()

    # The signal should no longer be 'pending'.
    remaining = store.list_pending_signals()
    assert all(s["id"] != sig_id for s in remaining)

    # No entry was attempted.
    blofin.place_market_entry.assert_not_called()
```

Update `_make_poller` helper (earlier in the same file) to accept and pass a `gate` kwarg:

```python
def _make_poller(store, blofin, **overrides):
    defaults = dict(
        store=store, blofin=blofin, interval_seconds=0,
        breakeven_usdt=15, trail_activate_usdt=25,
        trail_start_usdt=30, trail_distance_usdt=10,
        margin_usdt=100, leverage=30,
        gate=None,
    )
    defaults.update(overrides)
    return PositionPoller(**defaults)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_poller.py::test_pending_signal_for_paused_symbol_is_expired -v`
Expected: fail with `TypeError: PositionPoller.__init__() got an unexpected keyword argument 'gate'`.

- [ ] **Step 3: Add `gate` param + gate check to poller**

Edit `scripts/scalping/src/blofin_bridge/poller.py`:

Add to imports:
```python
from .entry_gate import EntryGate
```

Add to `PositionPoller.__init__` signature (after `symbol_configs`):
```python
        gate: Optional[EntryGate] = None,
```

And store it in `__init__` body:
```python
        self.gate = gate
```

In `_process_pending_signals`, add the gate check at the top of the per-signal loop, right after the expiry check:

```python
        for sig in signals:
            try:
                # Check expiry
                expires_at = datetime.fromisoformat(sig["expires_at"])
                if now >= expires_at:
                    self.store.expire_pending_signal(sig["id"])
                    log.info("Pending signal %d expired for %s", sig["id"], sig["symbol"])
                    if self.notifier:
                        self.notifier.send(format_pending_expired(sig["action"], sig["symbol"]))
                    continue

                # Operator pause: drop the pending signal without firing.
                if self.gate is not None and self.gate.is_paused(sig["symbol"]):
                    self.store.expire_pending_signal(sig["id"])
                    log.info(
                        "Pending signal %d for %s dropped: entries paused",
                        sig["id"], sig["symbol"],
                    )
                    continue

                # Fetch current price and EMA
                ...  # unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_poller.py -v`
Expected: all existing poller tests still pass + the new one.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/poller.py scripts/scalping/tests/test_poller.py
git commit -m "scalping: PositionPoller drops pending signals for paused symbols"
```

---

## Task 4: Config — add `telegram_allowed_user_id`

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/config.py`

- [ ] **Step 1: Add field to `BridgeCreds`**

Edit `scripts/scalping/src/blofin_bridge/config.py`. In the `BridgeCreds` class, add one field:

```python
class BridgeCreds(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
    )
    shared_secret: str = Field(alias="BRIDGE_SECRET")
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", default="")
    telegram_chat_id: str = Field(alias="TELEGRAM_CHAT_ID", default="")
    telegram_allowed_user_id: int = Field(alias="TG_ALLOWED_USER_ID", default=6421609315)
```

- [ ] **Step 2: Run existing config tests**

Run: `python -m pytest tests/ -q -k config`
Expected: pass (no new tests, just making sure we didn't break loading).

- [ ] **Step 3: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/config.py
git commit -m "scalping: config — TG_ALLOWED_USER_ID for Fin command auth"
```

---

## Task 5: `TelegramCommander` — HTTP layer + auth + update routing

**Files:**
- Create: `scripts/scalping/src/blofin_bridge/tg_commander.py`
- Create: `scripts/scalping/tests/test_tg_commander.py`

This task builds the commander's skeleton: HTTP helpers, update loop, auth filter, and dispatch to stub command handlers. The real command handlers arrive in Task 6 and callbacks/keyboard in Task 7.

- [ ] **Step 1: Write the failing test**

Create `scripts/scalping/tests/test_tg_commander.py`:

```python
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from blofin_bridge.entry_gate import EntryGate
from blofin_bridge.tg_commander import TelegramCommander


@pytest.fixture
def gate():
    return EntryGate(symbols=["SOL-USDT", "ZEC-USDT"])


@pytest.fixture
def store_mock():
    m = MagicMock()
    m.cancel_pending_signals_for_symbol.return_value = 0
    m.list_open_positions.return_value = []
    return m


@pytest.fixture
def commander(gate, store_mock):
    c = TelegramCommander(
        bot_token="TEST_TOKEN",
        allowed_user_id=111,
        gate=gate,
        store=store_mock,
    )
    # Replace HTTP calls with mocks
    c._send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 1}})
    c._edit_message = AsyncMock(return_value={"ok": True})
    c._answer_callback = AsyncMock(return_value={"ok": True})
    return c


def _text_update(text: str, user_id: int = 111, chat_id: int = 111) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "from": {"id": user_id},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


@pytest.mark.asyncio
async def test_unauthorized_user_is_silently_dropped(commander):
    await commander._handle_update(_text_update("/menu", user_id=999))
    commander._send_message.assert_not_called()
    commander._edit_message.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_user_reaches_dispatch(commander):
    # The dispatcher calls _cmd_menu which calls _send_message.
    await commander._handle_update(_text_update("/menu"))
    commander._send_message.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_command_replies_with_help(commander):
    await commander._handle_update(_text_update("/wat"))
    commander._send_message.assert_called_once()
    _, kwargs = commander._send_message.call_args
    assert "/menu" in kwargs["text"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: fail with `ModuleNotFoundError: No module named 'blofin_bridge.tg_commander'`.

- [ ] **Step 3: Write the skeleton implementation**

Create `scripts/scalping/src/blofin_bridge/tg_commander.py`:

```python
"""Telegram bot command listener for Fin (BloFin scalping bridge).

Long-polls `getUpdates` and dispatches slash commands + inline keyboard
callbacks to pause/resume per-symbol entries via EntryGate. Auth is
restricted to a single allowed Telegram user ID — unauthorized updates
are silently dropped so the bot's existence is not leaked.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional

import httpx

from .entry_gate import EntryGate
from .state import Store

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
LONG_POLL_TIMEOUT = 25  # seconds
POLL_BACKOFF_INITIAL = 5
POLL_BACKOFF_MAX = 60

# Alias → full symbol map. Populated from the EntryGate at construction.
SYMBOL_ALIASES: dict[str, str] = {}


def _build_alias_map(symbols: list[str]) -> dict[str, str]:
    """Map 'sol' / 'SOL' / 'SOL-USDT' → 'SOL-USDT'."""
    out: dict[str, str] = {}
    for sym in symbols:
        out[sym.lower()] = sym
        base = sym.split("-")[0].lower()
        out[base] = sym
    return out


class TelegramCommander:
    def __init__(
        self,
        *,
        bot_token: str,
        allowed_user_id: int,
        gate: EntryGate,
        store: Store,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_user_id = allowed_user_id
        self.gate = gate
        self.store = store
        self._aliases = _build_alias_map(sorted(gate.status().keys()))
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._offset: int = 0
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token) and self.allowed_user_id > 0

    # -------- lifecycle (fleshed out in Task 8) --------

    def start(self) -> None:
        raise NotImplementedError  # Task 8

    async def stop(self) -> None:
        raise NotImplementedError  # Task 8

    # -------- update handling --------

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")

        if message is not None:
            from_id = (message.get("from") or {}).get("id")
            if from_id != self.allowed_user_id:
                log.info("dropping message from unauthorized user %s", from_id)
                return
            chat_id = (message.get("chat") or {}).get("id")
            text = (message.get("text") or "").strip()
            await self._handle_command(chat_id=chat_id, text=text)
            return

        if callback is not None:
            # Full handling lives in Task 7.
            from_id = (callback.get("from") or {}).get("id")
            if from_id != self.allowed_user_id:
                log.info("dropping callback from unauthorized user %s", from_id)
                return
            await self._handle_callback(callback)
            return

    async def _handle_command(self, *, chat_id: int, text: str) -> None:
        """Dispatch a slash-command message. Populated in Task 6."""
        if not text.startswith("/"):
            return

        head, *rest = text.split(maxsplit=1)
        cmd = head.lower()
        arg = rest[0] if rest else ""

        if cmd == "/menu":
            await self._cmd_menu(chat_id)
        elif cmd == "/status":
            await self._cmd_status(chat_id)
        elif cmd == "/stop":
            await self._cmd_stop(chat_id, arg)
        elif cmd == "/start":
            await self._cmd_start(chat_id, arg)
        else:
            await self._send_message(
                chat_id=chat_id,
                text=(
                    "Unknown command. Try:\n"
                    "/menu — control keyboard\n"
                    "/status — current state\n"
                    "/stop <sol|zec|all>\n"
                    "/start <sol|zec|all>"
                ),
            )

    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        """Stub — Task 7 fills this in."""
        pass

    # -------- command handler stubs (Task 6 fills them in) --------

    async def _cmd_menu(self, chat_id: int) -> None:
        # Minimal stub so auth tests can see a _send_message call.
        await self._send_message(chat_id=chat_id, text="menu")

    async def _cmd_status(self, chat_id: int) -> None:
        await self._send_message(chat_id=chat_id, text="status")

    async def _cmd_stop(self, chat_id: int, arg: str) -> None:
        await self._send_message(chat_id=chat_id, text=f"stop {arg}")

    async def _cmd_start(self, chat_id: int, arg: str) -> None:
        await self._send_message(chat_id=chat_id, text=f"start {arg}")

    # -------- HTTP helpers --------

    async def _get_updates(self) -> list[dict[str, Any]]:
        if self._client is None:
            return []
        params = {
            "timeout": LONG_POLL_TIMEOUT,
            "offset": self._offset,
            "allowed_updates": ["message", "callback_query"],
        }
        r = await self._client.get(
            f"{API_BASE}/bot{self.bot_token}/getUpdates",
            params=params,
            timeout=LONG_POLL_TIMEOUT + 5,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("result", [])

    async def _send_message(
        self, *, chat_id: int, text: str,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if self._client is None:
            return {}
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/sendMessage",
            json=body,
            timeout=10.0,
        )
        return r.json()

    async def _edit_message(
        self, *, chat_id: int, message_id: int, text: str,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        if self._client is None:
            return {}
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/editMessageText",
            json=body,
            timeout=10.0,
        )
        return r.json()

    async def _answer_callback(
        self, *, callback_id: str, text: str = "",
    ) -> dict[str, Any]:
        if self._client is None:
            return {}
        r = await self._client.post(
            f"{API_BASE}/bot{self.bot_token}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=10.0,
        )
        return r.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/tg_commander.py scripts/scalping/tests/test_tg_commander.py
git commit -m "scalping: TelegramCommander skeleton — auth, update routing, HTTP helpers"
```

---

## Task 6: Real slash command handlers (`/menu`, `/status`, `/stop`, `/start`)

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/tg_commander.py`
- Modify: `scripts/scalping/tests/test_tg_commander.py`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/scalping/tests/test_tg_commander.py`:

```python
@pytest.mark.asyncio
async def test_stop_sol_pauses_sol_only(commander, gate, store_mock):
    store_mock.cancel_pending_signals_for_symbol.return_value = 2
    await commander._handle_update(_text_update("/stop sol"))

    assert gate.is_paused("SOL-USDT") is True
    assert gate.is_paused("ZEC-USDT") is False
    store_mock.cancel_pending_signals_for_symbol.assert_called_once_with("SOL-USDT")

    # Reply body mentions paused + cancelled count
    _, kwargs = commander._send_message.call_args
    assert "SOL" in kwargs["text"]
    assert "paused" in kwargs["text"].lower()
    assert "2" in kwargs["text"]


@pytest.mark.asyncio
async def test_stop_all_pauses_both(commander, gate, store_mock):
    await commander._handle_update(_text_update("/stop all"))
    assert gate.is_paused("SOL-USDT") is True
    assert gate.is_paused("ZEC-USDT") is True
    # store called once per symbol
    assert store_mock.cancel_pending_signals_for_symbol.call_count == 2


@pytest.mark.asyncio
async def test_start_sol_resumes_sol(commander, gate):
    await gate.pause("SOL-USDT")
    await commander._handle_update(_text_update("/start sol"))
    assert gate.is_paused("SOL-USDT") is False


@pytest.mark.asyncio
async def test_stop_unknown_alias_errors(commander):
    await commander._handle_update(_text_update("/stop doge"))
    _, kwargs = commander._send_message.call_args
    assert "unknown" in kwargs["text"].lower() or "unrecognized" in kwargs["text"].lower()


@pytest.mark.asyncio
async def test_status_lists_both_symbols(commander, gate):
    await gate.pause("SOL-USDT")
    await commander._handle_update(_text_update("/status"))
    _, kwargs = commander._send_message.call_args
    body = kwargs["text"]
    assert "SOL-USDT" in body
    assert "ZEC-USDT" in body
    assert "paused" in body.lower()
    assert "running" in body.lower()


@pytest.mark.asyncio
async def test_menu_sends_message_with_inline_keyboard(commander):
    await commander._handle_update(_text_update("/menu"))
    _, kwargs = commander._send_message.call_args
    assert "reply_markup" in kwargs
    kb = kwargs["reply_markup"]
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    # 3 rows: [Stop SOL | Stop ZEC], [Start SOL | Start ZEC], [Status]
    assert len(rows) == 3
    flat_texts = [btn["text"] for row in rows for btn in row]
    assert any("Stop" in t and "SOL" in t for t in flat_texts)
    assert any("Start" in t and "ZEC" in t for t in flat_texts)
    assert any("Status" in t for t in flat_texts)
```

- [ ] **Step 2: Run test to verify they fail**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: the 6 new tests fail (stubs don't do real work yet).

- [ ] **Step 3: Replace the stub command handlers + add helpers**

Edit `scripts/scalping/src/blofin_bridge/tg_commander.py`. Replace the stub `_cmd_*` methods and add rendering helpers:

```python
    # -------- command handlers --------

    def _render_status_text(self) -> str:
        lines = ["Fin — control"]
        for sym, paused in self.gate.status().items():
            tag = "⏸ paused" if paused else "▶ running"
            lines.append(f"{sym}: {tag}")
        return "\n".join(lines)

    def _render_keyboard(self) -> dict[str, Any]:
        status = self.gate.status()
        symbols = sorted(status.keys())  # ["SOL-USDT", "ZEC-USDT"]

        def stop_btn(sym: str) -> dict[str, str]:
            base = sym.split("-")[0]
            label = f"⏸ {base} paused" if status[sym] else f"⏸ Stop {base}"
            return {"text": label, "callback_data": f"stop:{sym}"}

        def start_btn(sym: str) -> dict[str, str]:
            base = sym.split("-")[0]
            label = f"▶ Start {base}" if status[sym] else f"▶ {base} running"
            return {"text": label, "callback_data": f"start:{sym}"}

        return {
            "inline_keyboard": [
                [stop_btn(symbols[0]), stop_btn(symbols[1])] if len(symbols) >= 2
                    else [stop_btn(symbols[0])],
                [start_btn(symbols[0]), start_btn(symbols[1])] if len(symbols) >= 2
                    else [start_btn(symbols[0])],
                [{"text": "📊 Status", "callback_data": "status"}],
            ]
        }

    def _resolve_alias(self, alias: str) -> Optional[str]:
        """Map 'sol' → 'SOL-USDT'. Returns None for unknown, 'all' for all."""
        key = alias.strip().lower()
        if key == "all":
            return "all"
        return self._aliases.get(key)

    async def _cmd_menu(self, chat_id: int) -> None:
        await self._send_message(
            chat_id=chat_id,
            text=self._render_status_text(),
            reply_markup=self._render_keyboard(),
        )

    async def _cmd_status(self, chat_id: int) -> None:
        await self._send_message(
            chat_id=chat_id,
            text=self._render_status_text(),
        )

    async def _cmd_stop(self, chat_id: int, arg: str) -> None:
        target = self._resolve_alias(arg)
        if target is None:
            await self._send_message(
                chat_id=chat_id,
                text=f"Unknown symbol '{arg}'. Try: sol, zec, all",
            )
            return

        if target == "all":
            cancelled_total = 0
            for sym in list(self.gate.status().keys()):
                await self.gate.pause(sym)
                cancelled_total += self.store.cancel_pending_signals_for_symbol(sym)
            await self._send_message(
                chat_id=chat_id,
                text=f"All symbols paused. {cancelled_total} pending signal(s) cancelled.",
            )
            return

        await self.gate.pause(target)
        cancelled = self.store.cancel_pending_signals_for_symbol(target)
        base = target.split("-")[0]
        await self._send_message(
            chat_id=chat_id,
            text=f"{base} paused. {cancelled} pending signal(s) cancelled.",
        )

    async def _cmd_start(self, chat_id: int, arg: str) -> None:
        target = self._resolve_alias(arg)
        if target is None:
            await self._send_message(
                chat_id=chat_id,
                text=f"Unknown symbol '{arg}'. Try: sol, zec, all",
            )
            return

        if target == "all":
            await self.gate.resume_all()
            await self._send_message(chat_id=chat_id, text="All symbols running.")
            return

        await self.gate.resume(target)
        base = target.split("-")[0]
        await self._send_message(chat_id=chat_id, text=f"{base} running.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: all 9 tests passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/tg_commander.py scripts/scalping/tests/test_tg_commander.py
git commit -m "scalping: Fin slash commands — /menu /status /stop /start"
```

---

## Task 7: Inline keyboard callback handler

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/tg_commander.py`
- Modify: `scripts/scalping/tests/test_tg_commander.py`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/scalping/tests/test_tg_commander.py`:

```python
def _callback_update(
    data: str, user_id: int = 111, chat_id: int = 111,
    message_id: int = 42,
) -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb1",
            "from": {"id": user_id},
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "text": "existing",
            },
        },
    }


@pytest.mark.asyncio
async def test_callback_stop_sol(commander, gate):
    await commander._handle_update(_callback_update("stop:SOL-USDT"))
    assert gate.is_paused("SOL-USDT") is True

    # Menu message edited to reflect new state
    commander._edit_message.assert_called_once()
    _, kwargs = commander._edit_message.call_args
    assert "SOL-USDT" in kwargs["text"]
    assert "paused" in kwargs["text"].lower()
    assert "reply_markup" in kwargs

    # Toast ack sent
    commander._answer_callback.assert_called_once_with(callback_id="cb1", text="SOL paused")


@pytest.mark.asyncio
async def test_callback_start_sol(commander, gate):
    await gate.pause("SOL-USDT")
    await commander._handle_update(_callback_update("start:SOL-USDT"))
    assert gate.is_paused("SOL-USDT") is False
    commander._edit_message.assert_called_once()
    commander._answer_callback.assert_called_once_with(callback_id="cb1", text="SOL running")


@pytest.mark.asyncio
async def test_callback_status_refreshes_menu(commander):
    await commander._handle_update(_callback_update("status"))
    commander._edit_message.assert_called_once()
    commander._answer_callback.assert_called_once()


@pytest.mark.asyncio
async def test_callback_from_unauthorized_user_is_dropped(commander):
    await commander._handle_update(_callback_update("stop:SOL-USDT", user_id=999))
    commander._edit_message.assert_not_called()
    commander._answer_callback.assert_not_called()
```

- [ ] **Step 2: Run test to verify they fail**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: the 4 new callback tests fail (handler is still the `pass` stub).

- [ ] **Step 3: Implement `_handle_callback`**

In `scripts/scalping/src/blofin_bridge/tg_commander.py`, replace the stub `_handle_callback` with:

```python
    async def _handle_callback(self, callback: dict[str, Any]) -> None:
        from_id = (callback.get("from") or {}).get("id")
        if from_id != self.allowed_user_id:
            log.info("dropping callback from unauthorized user %s", from_id)
            return

        cb_id: str = callback.get("id", "")
        data: str = callback.get("data", "")
        message = callback.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")

        action, _, target = data.partition(":")
        toast = ""

        try:
            if action == "stop" and target:
                await self.gate.pause(target)
                self.store.cancel_pending_signals_for_symbol(target)
                toast = f"{target.split('-')[0]} paused"
            elif action == "start" and target:
                await self.gate.resume(target)
                toast = f"{target.split('-')[0]} running"
            elif action == "status":
                toast = "Refreshed"
            else:
                toast = "Unknown action"
        except ValueError as exc:
            toast = f"Error: {exc}"

        await self._edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=self._render_status_text(),
            reply_markup=self._render_keyboard(),
        )
        await self._answer_callback(callback_id=cb_id, text=toast)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/tg_commander.py scripts/scalping/tests/test_tg_commander.py
git commit -m "scalping: Fin inline keyboard callbacks — tap-to-pause/resume"
```

---

## Task 8: Commander async lifecycle — `start()` / `stop()` / poll loop

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/tg_commander.py`
- Modify: `scripts/scalping/tests/test_tg_commander.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/scalping/tests/test_tg_commander.py`:

```python
@pytest.mark.asyncio
async def test_lifecycle_start_and_stop(gate, store_mock):
    """start() must schedule the poll loop and stop() must cancel it cleanly."""
    c = TelegramCommander(
        bot_token="TEST_TOKEN",
        allowed_user_id=111,
        gate=gate,
        store=store_mock,
    )
    # Fake the poll loop body so start() doesn't make real HTTP calls.
    c._poll_loop = AsyncMock()

    c.start()
    assert c._task is not None
    await asyncio.sleep(0)  # let the event loop schedule the task
    await c.stop()
    assert c._task is None


@pytest.mark.asyncio
async def test_poll_loop_handles_network_error(gate, store_mock, monkeypatch):
    """One getUpdates failure should be logged and recovered, not kill the loop."""
    c = TelegramCommander(
        bot_token="TEST_TOKEN",
        allowed_user_id=111,
        gate=gate,
        store=store_mock,
    )

    calls = {"n": 0}

    async def fake_get_updates():
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        # On second call: stop the loop cleanly by setting stop_event
        c._stop_event.set()
        return []

    c._get_updates = fake_get_updates  # type: ignore
    # Tighten the backoff so the test finishes fast.
    monkeypatch.setattr(
        "blofin_bridge.tg_commander.POLL_BACKOFF_INITIAL", 0,
    )

    c.start()
    await asyncio.wait_for(c._task, timeout=2.0)
    assert calls["n"] >= 2
```

Add the `httpx` import at the top of the test file if not already present:

```python
import httpx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tg_commander.py::test_lifecycle_start_and_stop -v`
Expected: fail with `NotImplementedError` from the `start()` stub.

- [ ] **Step 3: Implement `start()` / `stop()` / `_poll_loop()`**

In `scripts/scalping/src/blofin_bridge/tg_commander.py`, replace the lifecycle stubs with:

```python
    def start(self) -> None:
        if self._task is not None:
            return
        if not self.enabled:
            log.info("TelegramCommander disabled (no bot token)")
            return
        self._stop_event = asyncio.Event()
        self._client = httpx.AsyncClient()
        self._task = asyncio.create_task(
            self._poll_loop(), name="tg-commander-loop"
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._task = None
        self._stop_event = None

    async def _poll_loop(self) -> None:
        backoff = POLL_BACKOFF_INITIAL
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                updates = await self._get_updates()
                for update in updates:
                    self._offset = max(self._offset, update.get("update_id", 0) + 1)
                    try:
                        await self._handle_update(update)
                    except Exception:
                        log.exception("handler failed for update %s", update.get("update_id"))
                backoff = POLL_BACKOFF_INITIAL  # reset on success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("getUpdates failed: %s (backoff %ss)", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2 if backoff else 1, POLL_BACKOFF_MAX)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tg_commander.py -v`
Expected: all 15 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/tg_commander.py scripts/scalping/tests/test_tg_commander.py
git commit -m "scalping: TelegramCommander poll loop + graceful start/stop"
```

---

## Task 9: Wire `EntryGate` + `TelegramCommander` into `main.py` lifespan

**Files:**
- Modify: `scripts/scalping/src/blofin_bridge/main.py`

- [ ] **Step 1: Edit imports**

At the top of `main.py` (with the other `blofin_bridge` imports) add:

```python
from .entry_gate import EntryGate
from .tg_commander import TelegramCommander
```

- [ ] **Step 2: Construct the gate + commander in `create_app`**

Inside `create_app()`, right after `notifier` is built (around line 64):

```python
    gate = EntryGate(symbols=[name for name, c in settings.symbols.items() if c.enabled])

    commander = TelegramCommander(
        bot_token=settings.bridge.telegram_bot_token,
        allowed_user_id=settings.bridge.telegram_allowed_user_id,
        gate=gate,
        store=store,
    )
```

- [ ] **Step 3: Pass `gate` into the poller**

In the `PositionPoller(...)` constructor call, add `gate=gate,` as the last kwarg (right before the closing paren).

- [ ] **Step 4: Pass `gate` into `dispatch`**

In `_process_webhook`, update the dispatch call:

```python
            result = dispatch(
                action=payload.action, symbol=payload.symbol,
                store=store, blofin=blofin, symbol_configs=symbol_configs,
                gate=gate,
            )
```

- [ ] **Step 5: Handle the paused response in the webhook handler**

Still inside `_process_webhook`, add handling for the paused response before the existing `if result.get("pending"):` branch:

```python
            if result.get("paused"):
                log.info(
                    "webhook for %s %s ignored: entries paused",
                    payload.symbol, payload.action,
                )
                # No notifier.send here — silent by design to avoid alert spam.
                return
            if result.get("pending"):
                ...
```

- [ ] **Step 6: Start / stop commander in lifespan**

Replace the `lifespan` asynccontextmanager with:

```python
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        poller.start()
        commander.start()
        try:
            yield
        finally:
            await commander.stop()
            await poller.stop()
```

- [ ] **Step 7: Run the full test suite**

Run: `python -m pytest -q`
Expected: all tests pass (≥ 120 — 104 existing + new gate/commander/router/poller tests).

- [ ] **Step 8: Commit**

```bash
git add scripts/scalping/src/blofin_bridge/main.py
git commit -m "scalping: wire EntryGate + TelegramCommander into app lifespan"
```

---

## Task 10: Deploy to VPS and smoke-test from Telegram

**Files:** none (deployment only)

- [ ] **Step 1: Copy changed source files to VPS**

From `C:\Users\rakai\Leverage`:

```bash
scp \
  scripts/scalping/src/blofin_bridge/entry_gate.py \
  scripts/scalping/src/blofin_bridge/tg_commander.py \
  scripts/scalping/src/blofin_bridge/router.py \
  scripts/scalping/src/blofin_bridge/poller.py \
  scripts/scalping/src/blofin_bridge/config.py \
  scripts/scalping/src/blofin_bridge/main.py \
  root@46.202.146.30:/docker/scalping/src/blofin_bridge/
```

- [ ] **Step 2: Rebuild container**

```bash
ssh root@46.202.146.30 "cd /docker/scalping && docker compose up -d --build 2>&1 | tail -20"
```

Expected: `Container scalping Started`.

- [ ] **Step 3: Tail logs to confirm clean startup**

```bash
ssh root@46.202.146.30 "docker logs scalping --tail 40"
```

Expected: `Uvicorn running on http://0.0.0.0:8787` and no tracebacks. Should also see `TelegramCommander` start log line.

- [ ] **Step 4: Smoke-test from Telegram**

From the operator's phone, message Fin:

1. `/menu` → Fin posts a message with 5 buttons and current state `SOL-USDT: ▶ running / ZEC-USDT: ▶ running`.
2. Tap `⏸ Stop SOL` → message edits to `SOL-USDT: ⏸ paused`, toast shows "SOL paused".
3. `/status` → reply shows same state.
4. Tap `▶ Start SOL` → edits back to `SOL-USDT: ▶ running`.
5. `/stop all` → both symbols flip to paused, reply includes "0 pending signal(s) cancelled" (or the real count if one was queued).
6. `/start all` → both flip back to running.

- [ ] **Step 5: End-to-end pause verification**

While SOL is paused, wait for (or trigger via a manual `curl`) a TV webhook for SOL. Confirm in logs:

```
webhook for SOL-USDT buy ignored: entries paused
```

Confirm no entry opened on BloFin (check `/status?secret=...` and `docker logs scalping`).

- [ ] **Step 6: Final commit — spec + plan metadata update**

No code commit needed here — this step is just marking in the project memory that the feature is live.

```bash
git push
```

---

## Post-implementation notes

- Once live, update `memory/project_scalping_clone.md` to record: Fin now has a per-symbol TG kill switch, in-memory only, reachable via `/menu` + inline keyboard.
- If operator reports any staleness in the menu, the fix is usually that a different container/instance is also polling `getUpdates` on the same bot token — check for stray local dev instances.
