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
async def test_resume_unknown_symbol_raises(gate):
    with pytest.raises(ValueError):
        await gate.resume("DOGE-USDT")


@pytest.mark.asyncio
async def test_pause_idempotent(gate):
    await gate.pause("SOL-USDT")
    await gate.pause("SOL-USDT")  # second call should not error
    assert gate.is_paused("SOL-USDT") is True


def test_is_paused_unknown_symbol_returns_false(gate):
    # A symbol we don't know about cannot be paused, so treat as running.
    assert gate.is_paused("DOGE-USDT") is False
