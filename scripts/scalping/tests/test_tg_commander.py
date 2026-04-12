import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
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


# === Auth / update routing ===


@pytest.mark.asyncio
async def test_unauthorized_message_is_silently_dropped(commander):
    await commander._handle_update(_text_update("/menu", user_id=999))
    commander._send_message.assert_not_called()
    commander._edit_message.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_message_reaches_dispatch(commander):
    await commander._handle_update(_text_update("/menu"))
    commander._send_message.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_command_replies_with_help(commander):
    await commander._handle_update(_text_update("/wat"))
    commander._send_message.assert_called_once()
    _, kwargs = commander._send_message.call_args
    assert "/menu" in kwargs["text"]
    assert "/stop" in kwargs["text"]


@pytest.mark.asyncio
async def test_non_slash_text_is_ignored(commander):
    await commander._handle_update(_text_update("hello there"))
    commander._send_message.assert_not_called()


# === /stop and /start ===


@pytest.mark.asyncio
async def test_stop_sol_pauses_sol_only(commander, gate, store_mock):
    store_mock.cancel_pending_signals_for_symbol.return_value = 2
    await commander._handle_update(_text_update("/stop sol"))

    assert gate.is_paused("SOL-USDT") is True
    assert gate.is_paused("ZEC-USDT") is False
    store_mock.cancel_pending_signals_for_symbol.assert_called_once_with("SOL-USDT")

    _, kwargs = commander._send_message.call_args
    assert "SOL" in kwargs["text"]
    assert "paused" in kwargs["text"].lower()
    assert "2" in kwargs["text"]


@pytest.mark.asyncio
async def test_stop_all_pauses_both(commander, gate, store_mock):
    await commander._handle_update(_text_update("/stop all"))
    assert gate.is_paused("SOL-USDT") is True
    assert gate.is_paused("ZEC-USDT") is True
    assert store_mock.cancel_pending_signals_for_symbol.call_count == 2


@pytest.mark.asyncio
async def test_start_sol_resumes_sol(commander, gate):
    await gate.pause("SOL-USDT")
    await commander._handle_update(_text_update("/start sol"))
    assert gate.is_paused("SOL-USDT") is False


@pytest.mark.asyncio
async def test_start_all_resumes_both(commander, gate):
    await gate.pause_all()
    await commander._handle_update(_text_update("/start all"))
    assert gate.is_paused("SOL-USDT") is False
    assert gate.is_paused("ZEC-USDT") is False


@pytest.mark.asyncio
async def test_stop_unknown_alias_errors(commander):
    await commander._handle_update(_text_update("/stop doge"))
    _, kwargs = commander._send_message.call_args
    assert "unknown" in kwargs["text"].lower()


@pytest.mark.asyncio
async def test_stop_accepts_uppercase_alias(commander, gate):
    await commander._handle_update(_text_update("/stop SOL"))
    assert gate.is_paused("SOL-USDT") is True


# === /status and /menu ===


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
async def test_menu_sends_message_with_inline_keyboard(commander, gate):
    # Pause ZEC so its Start button is active (testing both label states).
    await gate.pause("ZEC-USDT")
    await commander._handle_update(_text_update("/menu"))
    _, kwargs = commander._send_message.call_args
    assert "reply_markup" in kwargs
    kb = kwargs["reply_markup"]
    assert "inline_keyboard" in kb
    rows = kb["inline_keyboard"]
    assert len(rows) == 3
    flat_texts = [btn["text"] for row in rows for btn in row]
    # SOL is running → Stop SOL active, SOL running shown on start row
    assert any("Stop" in t and "SOL" in t for t in flat_texts)
    # ZEC is paused → ZEC paused shown on stop row, Start ZEC active
    assert any("Start" in t and "ZEC" in t for t in flat_texts)
    assert any("Status" in t for t in flat_texts)


# === Callback queries (button taps) ===


@pytest.mark.asyncio
async def test_callback_stop_sol_pauses_and_edits(commander, gate):
    await commander._handle_update(_callback_update("stop:SOL-USDT"))
    assert gate.is_paused("SOL-USDT") is True

    commander._edit_message.assert_called_once()
    _, kwargs = commander._edit_message.call_args
    assert "SOL-USDT" in kwargs["text"]
    assert "paused" in kwargs["text"].lower()
    assert "reply_markup" in kwargs

    commander._answer_callback.assert_called_once_with(callback_id="cb1", text="SOL paused")


@pytest.mark.asyncio
async def test_callback_start_sol_resumes(commander, gate):
    await gate.pause("SOL-USDT")
    await commander._handle_update(_callback_update("start:SOL-USDT"))
    assert gate.is_paused("SOL-USDT") is False
    commander._edit_message.assert_called_once()
    commander._answer_callback.assert_called_once_with(callback_id="cb1", text="SOL running")


@pytest.mark.asyncio
async def test_callback_status_shows_alert_without_editing(commander, gate):
    """Status button cannot edit the message (Telegram rejects 'not modified'),
    so it must show the current state as a popup alert instead."""
    await gate.pause("SOL-USDT")
    await commander._handle_update(_callback_update("status"))

    # No edit — state didn't change, nothing to re-render.
    commander._edit_message.assert_not_called()

    # Popup alert with the rendered status text.
    commander._answer_callback.assert_called_once()
    _, kwargs = commander._answer_callback.call_args
    assert kwargs["show_alert"] is True
    assert "SOL-USDT" in kwargs["text"]
    assert "paused" in kwargs["text"].lower()


@pytest.mark.asyncio
async def test_callback_from_unauthorized_user_is_dropped(commander):
    await commander._handle_update(_callback_update("stop:SOL-USDT", user_id=999))
    commander._edit_message.assert_not_called()
    commander._answer_callback.assert_not_called()


# === Keyboard rendering ===


def test_keyboard_reflects_live_state(commander, gate):
    # Not async: _render_keyboard is sync.
    asyncio.get_event_loop().run_until_complete(gate.pause("SOL-USDT"))
    kb = commander._render_keyboard()
    flat = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
    # SOL should show as paused (not as "Stop SOL")
    assert any("SOL paused" in t for t in flat)
    # ZEC should show as a tappable Stop button
    assert any("Stop ZEC" in t for t in flat)


# === Async lifecycle ===


@pytest.mark.asyncio
async def test_lifecycle_start_and_stop(gate, store_mock):
    c = TelegramCommander(
        bot_token="TEST_TOKEN",
        allowed_user_id=111,
        gate=gate,
        store=store_mock,
    )
    c._poll_loop = AsyncMock()

    c.start()
    assert c._task is not None
    await asyncio.sleep(0)
    await c.stop()
    assert c._task is None


@pytest.mark.asyncio
async def test_poll_loop_handles_network_error(gate, store_mock, monkeypatch):
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
        # Second call: stop loop cleanly
        assert c._stop_event is not None
        c._stop_event.set()
        return []

    c._get_updates = fake_get_updates  # type: ignore
    monkeypatch.setattr("blofin_bridge.tg_commander.POLL_BACKOFF_INITIAL", 0)

    c._stop_event = asyncio.Event()
    c._client = httpx.AsyncClient()
    c._task = asyncio.create_task(c._poll_loop())
    try:
        await asyncio.wait_for(c._task, timeout=2.0)
    finally:
        await c._client.aclose()
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_disabled_commander_start_is_noop(gate, store_mock):
    c = TelegramCommander(
        bot_token="",  # disabled
        allowed_user_id=111,
        gate=gate,
        store=store_mock,
    )
    c.start()
    assert c._task is None
