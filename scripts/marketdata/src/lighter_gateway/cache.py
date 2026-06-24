from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class CachedResponse:
    status: int
    body: bytes
    content_type: str
    fetched_monotonic: float


class ResponseCache:
    """In-memory LRU store of upstream responses. Stores only; TTL/staleness
    decisions are the caller's (it knows per-path TTL and the clock)."""

    def __init__(self, capacity: int):
        self._cap = max(1, capacity)
        self._d: "OrderedDict[str, CachedResponse]" = OrderedDict()

    def get(self, key: str) -> CachedResponse | None:
        entry = self._d.get(key)
        if entry is not None:
            self._d.move_to_end(key)
        return entry

    def put(self, key: str, entry: CachedResponse) -> None:
        self._d[key] = entry
        self._d.move_to_end(key)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)
