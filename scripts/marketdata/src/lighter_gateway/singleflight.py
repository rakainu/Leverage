from __future__ import annotations
import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    """Deduplicate concurrent async calls sharing a key onto one execution."""

    def __init__(self):
        self._inflight: dict[str, asyncio.Future] = {}

    async def do(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            return await existing
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        try:
            result = await factory()
            fut.set_result(result)
            return result
        except Exception as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(key, None)
