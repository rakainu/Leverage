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
        """Return True if entries are blocked for this symbol. Unknown symbols return False."""
        return symbol in self._paused

    def status(self) -> dict[str, bool]:
        return {sym: (sym in self._paused) for sym in sorted(self._known)}

    async def pause(self, symbol: str) -> None:
        # _known is write-once (set in __init__) — safe to read outside the lock.
        if symbol not in self._known:
            raise ValueError(f"unknown symbol {symbol}")
        async with self._lock:
            self._paused.add(symbol)

    async def resume(self, symbol: str) -> None:
        # _known is write-once (set in __init__) — safe to read outside the lock.
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
